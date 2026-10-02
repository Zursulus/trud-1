from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .access_resolver import can_any
from .models import ResidentAppeal
from .resident_models import ResidentAppealAttachment, ResidentAppealBoardMessage


OPEN_APPEAL_STATES = ("new", "in_progress", "awaiting_resident")
FINAL_APPEAL_STATES = ("resolved", "closed")
STAFF_REPLY_STATES = ("in_progress", "awaiting_resident", "resolved")


def _require_capability(actor, capability):
    if not actor.is_staff or not can_any(actor, capability):
        raise PermissionDenied


def send_board_reply(*, appeal_id, actor, body, document=None, next_status=None):
    """Append an immutable staff reply and apply the requested workflow state atomically.

    The final resolved reply remains in ResidentAppeal.response for compatibility with
    the resident portal and existing audited admin records. Non-final replies use the
    immutable ResidentAppealBoardMessage stream.
    """
    _require_capability(actor, "appeals.reply")
    if document:
        _require_capability(actor, "appeals.attachment.manage")
    if next_status is not None:
        _require_capability(actor, "appeals.status.change")
    text = (body or "").strip()
    if not text:
        raise ValidationError("Введите сообщение жителю.")
    if next_status not in (None, *STAFF_REPLY_STATES):
        raise ValidationError("Недопустимое состояние обращения.")

    with transaction.atomic():
        appeal = ResidentAppeal.objects.select_for_update().get(pk=appeal_id)
        if appeal.status in FINAL_APPEAL_STATES:
            raise ValidationError("Обращение уже завершено. Для нового вопроса нужен новый диалог.")

        if next_status == "resolved":
            appeal.response = text
            appeal.status = "resolved"
            appeal.responded_at = timezone.now()
            appeal.responded_by = actor
            appeal._history_user = actor
            appeal._change_reason = "Итоговый ответ правления и решение обращения"
            appeal.save()
            if document:
                ResidentAppealAttachment.objects.create(
                    appeal=appeal,
                    uploaded_by=actor,
                    document=document,
                )
            return appeal, None

        message = ResidentAppealBoardMessage.objects.create(
            appeal=appeal,
            author=actor,
            body=text,
        )
        if document:
            ResidentAppealAttachment.objects.create(
                appeal=appeal,
                board_message=message,
                uploaded_by=actor,
                document=document,
            )

        target = next_status
        if target is None and appeal.status == "new":
            target = "in_progress"
        if target and target != appeal.status:
            appeal.status = target
            appeal._history_user = actor
            appeal._change_reason = {
                "in_progress": "Правление продолжило работу по обращению",
                "awaiting_resident": "Правление запросило уточнение у жителя",
            }[target]
            appeal.save()
        return appeal, message


def close_resolved_appeal(*, appeal_id, actor):
    _require_capability(actor, "appeals.close")
    with transaction.atomic():
        appeal = ResidentAppeal.objects.select_for_update().get(pk=appeal_id)
        if appeal.status == "closed":
            return appeal
        if appeal.status != "resolved" or not appeal.response.strip():
            raise ValidationError("Закрыть можно только уже решённое обращение с итоговым ответом.")
        appeal.status = "closed"
        appeal._history_user = actor
        appeal._change_reason = "Решённое обращение закрыто правлением"
        appeal.save()
        return appeal


def latest_board_event_at(appeal):
    timestamps = []
    if appeal.responded_at and appeal.response.strip():
        timestamps.append(appeal.responded_at)
    latest_message = appeal.board_messages.order_by("-created_at", "-id").first()
    if latest_message:
        timestamps.append(latest_message.created_at)
    return max(timestamps) if timestamps else None


def appeal_conversation_events(appeal):
    """Build one chronological conversation stream for resident and staff UIs."""
    attachments = list(
        ResidentAppealAttachment.objects.filter(appeal=appeal)
        .select_related("uploaded_by", "message", "board_message")
        .order_by("created_at", "id")
    )
    initial_attachments = [
        item for item in attachments
        if not item.message_id and not item.board_message_id and not item.is_board_file
    ]
    resident_attachment_map = {}
    board_attachment_map = {}
    legacy_board_attachments = []
    for item in attachments:
        if item.message_id:
            resident_attachment_map.setdefault(item.message_id, []).append(item)
        elif item.board_message_id:
            board_attachment_map.setdefault(item.board_message_id, []).append(item)
        elif item.is_board_file:
            legacy_board_attachments.append(item)

    events = [{
        "kind": "resident",
        "body": appeal.message,
        "created_at": appeal.opened_at,
        "attachments": initial_attachments,
    }]
    for message in appeal.resident_messages.all():
        events.append({
            "kind": "resident",
            "body": message.body,
            "created_at": message.created_at,
            "attachments": resident_attachment_map.get(message.pk, []),
        })
    if appeal.response.strip() and appeal.responded_at:
        events.append({
            "kind": "board",
            "body": appeal.response,
            "created_at": appeal.responded_at,
            "attachments": legacy_board_attachments,
            "author": appeal.responded_by,
            "final": True,
        })
    elif legacy_board_attachments:
        events.append({
            "kind": "board",
            "body": "",
            "created_at": legacy_board_attachments[0].created_at,
            "attachments": legacy_board_attachments,
            "final": True,
        })
    for message in appeal.board_messages.select_related("author").all():
        events.append({
            "kind": "board",
            "body": message.body,
            "created_at": message.created_at,
            "attachments": board_attachment_map.get(message.pk, []),
            "author": message.author,
        })
    events.sort(key=lambda item: item["created_at"])
    return events
