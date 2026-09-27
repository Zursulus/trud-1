from collections import Counter

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from .board_polls import (
    BoardAuditEvent,
    BoardDiscussionComment,
    BoardMembership,
    BoardPoll,
    BoardProtocol,
    BoardQuestion,
    BoardVote,
)
from .staff_workspace import _base_context


DATETIME_FORMAT = "%Y-%m-%dT%H:%M"


def _can(user, permission):
    return user.is_superuser or user.has_perm(permission)


def _require_workspace(request):
    if not request.user.is_staff or not _can(request.user, "water.view_boardpoll"):
        raise PermissionDenied


def _active_members_at(on_date):
    return BoardMembership.objects.filter(
        starts__lte=on_date,
        user__is_active=True,
    ).filter(
        Q(ends__isnull=True) | Q(ends__gt=on_date),
    ).select_related("user").order_by("user__last_name", "user__first_name", "user__username")


def _user_label(user):
    return user.get_full_name().strip() or user.username


def _question_rows(poll):
    opening_date = timezone.localtime(poll.opens_at).date()
    eligible_users = [membership.user for membership in _active_members_at(opening_date)]
    eligible_ids = {user.pk for user in eligible_users}
    rows = []
    for question in poll.questions.prefetch_related("votes__user", "discussion_comments__author").order_by("order", "id"):
        votes = list(question.votes.select_related("user").order_by("user__last_name", "user__first_name", "user__username"))
        counts = Counter(vote.choice for vote in votes)
        voted_ids = {vote.user_id for vote in votes}
        rows.append({
            "question": question,
            "votes": votes,
            "for_count": counts[BoardVote.CHOICE_FOR],
            "against_count": counts[BoardVote.CHOICE_AGAINST],
            "abstain_count": counts[BoardVote.CHOICE_ABSTAIN],
            "not_voted": [_user_label(user) for user in eligible_users if user.pk not in voted_ids],
            "eligible_count": len(eligible_ids),
            "comments": list(question.discussion_comments.select_related("author").all()),
        })
    return rows


def _audit_rows(poll, questions, protocol):
    question_ids = [row["question"].pk for row in questions]
    vote_ids = list(BoardVote.objects.filter(question_id__in=question_ids).values_list("pk", flat=True))
    comment_ids = list(BoardDiscussionComment.objects.filter(question_id__in=question_ids).values_list("pk", flat=True))
    criteria = Q(target_type="boardpoll", target_id=poll.pk)
    if question_ids:
        criteria |= Q(target_type="boardquestion", target_id__in=question_ids)
    if vote_ids:
        criteria |= Q(target_type="boardvote", target_id__in=vote_ids)
    if comment_ids:
        criteria |= Q(target_type="boarddiscussioncomment", target_id__in=comment_ids)
    if protocol is not None:
        criteria |= Q(target_type="boardprotocol", target_id=protocol.pk)
    return list(BoardAuditEvent.objects.filter(criteria).select_related("actor").order_by("-created_at", "-id")[:100])


class PollCreateForm(forms.Form):
    title = forms.CharField(label="Название", max_length=200)
    description = forms.CharField(label="Пояснение", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    opens_at = forms.DateTimeField(
        label="Начало",
        input_formats=[DATETIME_FORMAT],
        widget=forms.DateTimeInput(format=DATETIME_FORMAT, attrs={"type": "datetime-local"}),
    )
    closes_at = forms.DateTimeField(
        label="Срок ответа",
        input_formats=[DATETIME_FORMAT],
        widget=forms.DateTimeInput(format=DATETIME_FORMAT, attrs={"type": "datetime-local"}),
    )
    questions = forms.CharField(
        label="Вопросы — по одному на строку",
        widget=forms.Textarea(attrs={"rows": 7}),
        help_text="Порядок строк станет порядком вопросов. После появления голосов смысл вопроса менять нельзя.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            now = timezone.localtime().replace(second=0, microsecond=0)
            self.initial.update({
                "opens_at": now.strftime(DATETIME_FORMAT),
                "closes_at": (now + timezone.timedelta(days=3)).strftime(DATETIME_FORMAT),
            })

    def clean(self):
        cleaned = super().clean()
        opens_at = cleaned.get("opens_at")
        closes_at = cleaned.get("closes_at")
        if opens_at and closes_at and closes_at <= opens_at:
            self.add_error("closes_at", "Срок ответа должен быть позже начала опроса.")
        raw = cleaned.get("questions") or ""
        questions = [" ".join(line.split()) for line in raw.splitlines() if line.strip()]
        if not questions:
            self.add_error("questions", "Добавьте хотя бы один вопрос.")
        if len(questions) > 20:
            self.add_error("questions", "В одном предварительном опросе допускается не больше 20 вопросов.")
        if any(len(question) > 1000 for question in questions):
            self.add_error("questions", "Каждый вопрос должен быть не длиннее 1000 символов.")
        cleaned["question_lines"] = questions
        return cleaned


class ClosePollForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)


class ProtocolUploadForm(forms.Form):
    document = forms.FileField(label="PDF или DOCX")


@transaction.atomic
def _create_poll(form, actor):
    poll = BoardPoll(
        title=form.cleaned_data["title"],
        description=form.cleaned_data["description"],
        opens_at=form.cleaned_data["opens_at"],
        closes_at=form.cleaned_data["closes_at"],
        created_by=actor,
    )
    poll._audit_actor = actor
    poll._audit_reason = "Создание предварительного опроса в Рабочей базе"
    poll.save()
    for order, text in enumerate(form.cleaned_data["question_lines"], start=1):
        question = BoardQuestion(poll=poll, order=order, text=text)
        question._audit_actor = actor
        question._audit_reason = "Вопрос предварительного опроса"
        question.save()
    return poll


def dashboard(request):
    _require_workspace(request)
    now = timezone.now()
    polls = list(BoardPoll.objects.prefetch_related("questions").order_by("-opens_at", "-id")[:60])
    rows = []
    for poll in polls:
        question_ids = [question.pk for question in poll.questions.all()]
        vote_count = BoardVote.objects.filter(question_id__in=question_ids).count() if question_ids else 0
        rows.append({
            "poll": poll,
            "question_count": len(question_ids),
            "vote_count": vote_count,
            "is_open": poll.is_open,
            "is_scheduled": poll.closed_at is None and poll.opens_at > now,
            "is_closed": poll.closed_at is not None or poll.closes_at <= now,
        })
    context = _base_context(request, section="more")
    context.update({
        "poll_rows": rows,
        "can_add_poll": _can(request.user, "water.add_boardpoll") and _can(request.user, "water.add_boardquestion"),
        "open_count": sum(1 for row in rows if row["is_open"]),
        "closed_count": sum(1 for row in rows if row["is_closed"]),
    })
    return TemplateResponse(request, "water/work/governance/dashboard.html", context)


def poll_create(request):
    _require_workspace(request)
    if not (_can(request.user, "water.add_boardpoll") and _can(request.user, "water.add_boardquestion")):
        raise PermissionDenied
    form = PollCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            poll = _create_poll(form, request.user)
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
        else:
            messages.success(request, "Предварительный опрос создан. Это внутренний неофициальный контур правления.")
            return HttpResponseRedirect(reverse("staff_workspace:governance_poll", args=[poll.pk]))
    context = _base_context(request, section="more")
    context.update({"form": form})
    return TemplateResponse(request, "water/work/governance/form.html", context)


def _close_poll(request, poll, form):
    if not _can(request.user, "water.change_boardpoll"):
        raise PermissionDenied
    if not form.is_valid():
        return False
    with transaction.atomic():
        locked = BoardPoll.objects.select_for_update().get(pk=poll.pk)
        if locked.version != form.cleaned_data["version"]:
            form.add_error(None, "Опрос уже изменён. Обновите страницу и повторите действие.")
            return False
        if locked.closed_at is not None:
            form.add_error(None, "Опрос уже закрыт.")
            return False
        if timezone.now() < locked.opens_at:
            form.add_error(None, "Нельзя закрыть опрос до его начала.")
            return False
        locked.closed_at = timezone.now()
        locked._audit_actor = request.user
        locked._audit_reason = "Закрытие предварительного опроса в Рабочей базе"
        try:
            locked.save()
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
            return False
    messages.success(request, "Предварительный опрос закрыт. Голоса и вопросы сохранены для аудита.")
    return True


def _save_protocol(request, poll, form):
    if not _can(request.user, "water.add_boardprotocol"):
        raise PermissionDenied
    if not form.is_valid():
        return False
    if BoardProtocol.objects.filter(poll=poll).exists():
        form.add_error(None, "Протокол уже связан с этим опросом и не может быть заменён.")
        return False
    protocol = BoardProtocol(poll=poll, document=form.cleaned_data["document"], uploaded_by=request.user)
    try:
        protocol.save()
    except ValidationError as error:
        for message in error.messages:
            form.add_error(None, message)
        return False
    messages.success(request, "Протокол связан с опросом как неизменяемый приватный файл.")
    return True


def poll_detail(request, poll_id):
    _require_workspace(request)
    poll = get_object_or_404(BoardPoll.objects.select_related("created_by"), pk=poll_id)
    close_form = ClosePollForm(initial={"version": poll.version})
    protocol_form = ProtocolUploadForm()
    response_status = 200

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "close":
            close_form = ClosePollForm(request.POST)
            if _close_poll(request, poll, close_form):
                return HttpResponseRedirect(reverse("staff_workspace:governance_poll", args=[poll.pk]))
            response_status = 400
        elif action == "protocol":
            protocol_form = ProtocolUploadForm(request.POST, request.FILES)
            if _save_protocol(request, poll, protocol_form):
                return HttpResponseRedirect(reverse("staff_workspace:governance_poll", args=[poll.pk]))
            response_status = 400
        else:
            raise Http404
        poll.refresh_from_db()

    question_rows = _question_rows(poll)
    protocol = BoardProtocol.objects.filter(poll=poll).select_related("uploaded_by").first()
    context = _base_context(request, section="more")
    context.update({
        "poll": poll,
        "question_rows": question_rows,
        "protocol": protocol,
        "audit_rows": _audit_rows(poll, question_rows, protocol),
        "close_form": close_form,
        "protocol_form": protocol_form,
        "can_close": _can(request.user, "water.change_boardpoll")
        and poll.closed_at is None
        and timezone.now() >= poll.opens_at,
        "can_upload_protocol": _can(request.user, "water.add_boardprotocol")
        and not poll.is_open
        and protocol is None,
    })
    return TemplateResponse(request, "water/work/governance/detail.html", context, status=response_status)


def protocol_download(request, poll_id):
    _require_workspace(request)
    if not _can(request.user, "water.view_boardprotocol"):
        raise PermissionDenied
    protocol = get_object_or_404(BoardProtocol, poll_id=poll_id)
    try:
        stream = protocol.document.open("rb")
    except (FileNotFoundError, OSError) as error:
        raise Http404 from error
    response = FileResponse(stream, as_attachment=True, filename=protocol.original_name)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


workspace_governance = admin.site.admin_view(dashboard)
workspace_governance_poll_create = admin.site.admin_view(poll_create)
workspace_governance_poll = admin.site.admin_view(poll_detail)
workspace_governance_protocol = admin.site.admin_view(protocol_download)
