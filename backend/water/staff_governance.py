from collections import Counter
from datetime import timedelta

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from .board_poll_views import _eligible_users, _user_label
from .board_polls import (
    BoardAuditEvent,
    BoardPoll,
    BoardProtocol,
    BoardQuestion,
    BoardVote,
)
from .staff_workspace import _base_context


class BoardPollCreateForm(forms.Form):
    title = forms.CharField(label="Название", max_length=200)
    description = forms.CharField(
        label="Пояснение",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    closes_at = forms.DateTimeField(
        label="Срок ответа",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    questions = forms.CharField(
        label="Вопросы — по одному в строке",
        widget=forms.Textarea(attrs={"rows": 7}),
        help_text="Порядок строк станет порядком вопросов. После появления голосов смысл вопроса менять нельзя.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            initial = timezone.localtime(timezone.now() + timedelta(days=7)).replace(second=0, microsecond=0)
            self.fields["closes_at"].initial = initial

    def clean_closes_at(self):
        closes_at = self.cleaned_data["closes_at"]
        if closes_at <= timezone.now():
            raise forms.ValidationError("Срок ответа должен быть в будущем.")
        return closes_at

    def clean_questions(self):
        questions = [line.strip() for line in self.cleaned_data["questions"].splitlines() if line.strip()]
        if not questions:
            raise forms.ValidationError("Добавьте хотя бы один вопрос.")
        if len(questions) > 30:
            raise forms.ValidationError("В одном предварительном опросе допускается не больше 30 вопросов.")
        if any(len(question) > 1000 for question in questions):
            raise forms.ValidationError("Каждый вопрос должен быть не длиннее 1000 символов.")
        return questions


class BoardPollEditForm(forms.Form):
    title = forms.CharField(label="Название", max_length=200)
    description = forms.CharField(
        label="Пояснение",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    closes_at = forms.DateTimeField(
        label="Срок ответа",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, poll=None, **kwargs):
        super().__init__(*args, **kwargs)
        if poll is not None and not self.is_bound:
            self.initial.update({
                "title": poll.title,
                "description": poll.description,
                "closes_at": timezone.localtime(poll.closes_at).replace(second=0, microsecond=0),
                "version": poll.version,
            })

    def clean_closes_at(self):
        closes_at = self.cleaned_data["closes_at"]
        if closes_at <= timezone.now():
            raise forms.ValidationError("Срок ответа должен быть в будущем.")
        return closes_at


class BoardProtocolUploadForm(forms.Form):
    document = forms.FileField(label="Протокол PDF или DOCX")


def _can(user, permission):
    return user.is_superuser or user.has_perm(permission)


def _require_governance_view(request):
    if not request.user.is_staff or not _can(request.user, "water.view_boardpoll"):
        raise PermissionDenied


def _question_rows(poll, eligible_users):
    rows = []
    for question in poll.questions.all().order_by("order", "id"):
        votes = list(question.votes.select_related("user").all())
        counts = Counter(vote.choice for vote in votes)
        voter_ids = {vote.user_id for vote in votes}
        rows.append({
            "question": question,
            "votes": votes,
            "counts": {
                "for": counts[BoardVote.CHOICE_FOR],
                "against": counts[BoardVote.CHOICE_AGAINST],
                "abstain": counts[BoardVote.CHOICE_ABSTAIN],
            },
            "not_voted": [_user_label(user) for user in eligible_users if user.pk not in voter_ids],
            "comments": list(question.discussion_comments.select_related("author").all()),
        })
    return rows


def _audit_events(poll):
    question_ids = list(poll.questions.values_list("id", flat=True))
    vote_ids = list(BoardVote.objects.filter(question_id__in=question_ids).values_list("id", flat=True))
    comment_ids = list(
        poll.questions.values_list("discussion_comments__id", flat=True).exclude(discussion_comments__id__isnull=True)
    )
    query = Q(target_type="boardpoll", target_id=poll.pk)
    if question_ids:
        query |= Q(target_type="boardquestion", target_id__in=question_ids)
    if vote_ids:
        query |= Q(target_type="boardvote", target_id__in=vote_ids)
    if comment_ids:
        query |= Q(target_type="boarddiscussioncomment", target_id__in=comment_ids)
    protocol_id = BoardProtocol.objects.filter(poll=poll).values_list("id", flat=True).first()
    if protocol_id:
        query |= Q(target_type="boardprotocol", target_id=protocol_id)
    return list(BoardAuditEvent.objects.filter(query).select_related("actor").order_by("-created_at", "-id")[:100])


def _poll_detail_context(request, poll, *, edit_form=None, protocol_form=None):
    eligible = _eligible_users(poll)
    can_change = _can(request.user, "water.change_boardpoll") and poll.is_open
    context = _base_context(request, section="governance")
    context.update({
        "poll": poll,
        "eligible_count": len(eligible),
        "eligible_names": [_user_label(user) for user in eligible],
        "question_rows": _question_rows(poll, eligible),
        "protocol": BoardProtocol.objects.filter(poll=poll).first(),
        "edit_form": edit_form or BoardPollEditForm(poll=poll),
        "protocol_form": protocol_form or BoardProtocolUploadForm(),
        "audit_events": _audit_events(poll),
        "can_edit": can_change,
        "can_close": can_change,
        "can_upload_protocol": _can(request.user, "water.add_boardprotocol") and not poll.is_open,
    })
    return context


def governance_dashboard(request):
    _require_governance_view(request)
    polls = list(BoardPoll.objects.prefetch_related("questions__votes").order_by("-opens_at", "-id")[:50])
    rows = []
    for poll in polls:
        questions = list(poll.questions.all())
        eligible = _eligible_users(poll)
        question_ids = [question.pk for question in questions]
        vote_counts = Counter(
            BoardVote.objects.filter(question_id__in=question_ids).values_list("user_id", flat=True)
        ) if question_ids else Counter()
        responded = sum(1 for user in eligible if len(question_ids) and vote_counts[user.pk] >= len(question_ids))
        rows.append({
            "poll": poll,
            "question_count": len(question_ids),
            "eligible_count": len(eligible),
            "responded_count": responded,
            "pending_count": max(0, len(eligible) - responded),
        })
    context = _base_context(request, section="governance")
    context.update({
        "poll_rows": rows,
        "can_create_poll": _can(request.user, "water.add_boardpoll") and _can(request.user, "water.add_boardquestion"),
    })
    return TemplateResponse(request, "water/work/governance/dashboard.html", context)


def governance_create(request):
    _require_governance_view(request)
    if not (_can(request.user, "water.add_boardpoll") and _can(request.user, "water.add_boardquestion")):
        raise PermissionDenied
    form = BoardPollCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                poll = BoardPoll(
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data["description"],
                    opens_at=timezone.now(),
                    closes_at=form.cleaned_data["closes_at"],
                    created_by=request.user,
                )
                poll._audit_actor = request.user
                poll._audit_reason = "Создание предварительного опроса в рабочей базе"
                poll.save()
                for order, text in enumerate(form.cleaned_data["questions"], start=1):
                    question = BoardQuestion(poll=poll, order=order, text=text)
                    question._audit_actor = request.user
                    question._audit_reason = "Вопрос предварительного опроса"
                    question.save()
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
        else:
            messages.success(request, "Предварительный неофициальный опрос создан.")
            return HttpResponseRedirect(reverse("staff_workspace:governance_detail", args=[poll.pk]))
    context = _base_context(request, section="governance")
    context["form"] = form
    return TemplateResponse(request, "water/work/governance/new.html", context)


def governance_detail(request, poll_id):
    _require_governance_view(request)
    poll = get_object_or_404(BoardPoll.objects.prefetch_related("questions__votes__user", "questions__discussion_comments__author"), pk=poll_id)
    edit_form = BoardPollEditForm(poll=poll)
    protocol_form = BoardProtocolUploadForm()

    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "edit":
            if not _can(request.user, "water.change_boardpoll"):
                raise PermissionDenied
            edit_form = BoardPollEditForm(request.POST, poll=poll)
            if edit_form.is_valid():
                try:
                    with transaction.atomic():
                        locked = BoardPoll.objects.select_for_update().get(pk=poll.pk)
                        if edit_form.cleaned_data["version"] != locked.version:
                            raise ValidationError("Опрос уже изменён. Обновите страницу перед сохранением.")
                        if not locked.is_open:
                            raise ValidationError("Изменять через рабочую базу можно только открытый предварительный опрос.")
                        locked.title = edit_form.cleaned_data["title"]
                        locked.description = edit_form.cleaned_data["description"]
                        locked.closes_at = edit_form.cleaned_data["closes_at"]
                        locked._audit_actor = request.user
                        locked._audit_reason = "Изменение метаданных предварительного опроса"
                        locked.save()
                except ValidationError as error:
                    edit_form.add_error(None, "; ".join(error.messages))
                else:
                    messages.success(request, "Параметры предварительного опроса обновлены.")
                    return HttpResponseRedirect(reverse("staff_workspace:governance_detail", args=[poll.pk]))

        elif action == "close":
            if not _can(request.user, "water.change_boardpoll"):
                raise PermissionDenied
            try:
                submitted_version = int(request.POST.get("version", ""))
            except (TypeError, ValueError) as error:
                raise Http404 from error
            try:
                with transaction.atomic():
                    locked = BoardPoll.objects.select_for_update().get(pk=poll.pk)
                    if submitted_version != locked.version:
                        raise ValidationError("Опрос уже изменён. Обновите страницу перед закрытием.")
                    if not locked.is_open:
                        raise ValidationError("Закрыть можно только открытый предварительный опрос.")
                    locked.closed_at = timezone.now()
                    locked._audit_actor = request.user
                    locked._audit_reason = "Ручное закрытие предварительного опроса"
                    locked.save()
            except ValidationError as error:
                messages.error(request, "; ".join(error.messages))
            else:
                messages.success(request, "Предварительный опрос закрыт. Голоса и обсуждение больше не меняются.")
            return HttpResponseRedirect(reverse("staff_workspace:governance_detail", args=[poll.pk]))

        elif action == "upload_protocol":
            if not _can(request.user, "water.add_boardprotocol"):
                raise PermissionDenied
            if poll.is_open or BoardProtocol.objects.filter(poll=poll).exists():
                raise PermissionDenied
            protocol_form = BoardProtocolUploadForm(request.POST, request.FILES)
            if protocol_form.is_valid():
                try:
                    BoardProtocol.objects.create(
                        poll=poll,
                        document=protocol_form.cleaned_data["document"],
                        uploaded_by=request.user,
                    )
                except ValidationError as error:
                    protocol_form.add_error("document", "; ".join(error.messages))
                else:
                    messages.success(request, "Протокол сохранён. Заменить его через рабочую базу нельзя.")
                    return HttpResponseRedirect(reverse("staff_workspace:governance_detail", args=[poll.pk]))
        else:
            raise Http404

    poll.refresh_from_db()
    return TemplateResponse(
        request,
        "water/work/governance/detail.html",
        _poll_detail_context(request, poll, edit_form=edit_form, protocol_form=protocol_form),
    )


workspace_governance = admin.site.admin_view(governance_dashboard)
workspace_governance_create = admin.site.admin_view(governance_create)
workspace_governance_detail = admin.site.admin_view(governance_detail)
