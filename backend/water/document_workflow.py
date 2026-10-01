from django.core.exceptions import ValidationError
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from .access_scope import can_on_record
from .models import AccountDocument


def _require(actor, capability, *, account_id):
    if not actor.is_staff or not can_on_record(actor, capability, account_id=account_id):
        raise PermissionDenied


def _required_reason(value):
    reason = (value or "").strip()
    if not reason:
        raise ValidationError("Причина изменения: обязательно заполнить.")
    return reason


@transaction.atomic
def create_account_document(*, account, category, title, document, published_at, visible_to_residents, notes, actor):
    _require(actor, "documents.account.create", account_id=account.pk)
    item = AccountDocument(
        account=account,
        category=category,
        title=(title or "").strip(),
        document=document,
        published_at=published_at or timezone.now(),
        visible_to_residents=bool(visible_to_residents),
        notes=(notes or "").strip(),
    )
    item._history_user = actor
    item._change_reason = "Документ лицевого счёта создан через Рабочую базу"
    item.save()
    return item


@transaction.atomic
def update_account_document(
    document_id,
    *,
    category,
    title,
    published_at,
    visible_to_residents,
    notes,
    change_reason,
    actor,
):
    """Change only metadata/visibility; the stored file is deliberately immutable."""
    item = AccountDocument.objects.select_for_update().get(pk=document_id)
    _require(actor, "documents.account.edit_metadata", account_id=item.account_id)
    item.category = category
    item.title = (title or "").strip()
    item.published_at = published_at
    item.visible_to_residents = bool(visible_to_residents)
    item.notes = (notes or "").strip()
    item._history_user = actor
    item._change_reason = _required_reason(change_reason)
    item.save()
    return item
