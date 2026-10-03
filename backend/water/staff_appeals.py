from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse

from .access_resolver import can_any
from .appeal_admin_tools import download_appeal_attachment
from .appeal_security import (
    APPEAL_ATTACHMENT_HELP,
    record_form_upload_rejection,
    validate_appeal_attachment,
)
from .appeal_workflow import (
    FINAL_APPEAL_STATES,
    OPEN_APPEAL_STATES,
    appeal_conversation_events,
    close_resolved_appeal,
    send_board_reply,
    scoped_appeals,
    can_for_appeal,
)
from .models import ResidentAppeal
from .staff_workspace import _base_context

FILE_ACCEPT = '.pdf,.jpg,.jpeg,.png,.docx,.xlsx'


class StaffAppealReplyForm(forms.Form):
    body = forms.CharField(
        label="Сообщение жителю",
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 6}),
    )
    document = forms.FileField(
        label="Вложение",
        required=False,
        help_text=f"Необязательно. {APPEAL_ATTACHMENT_HELP}",
        validators=[validate_appeal_attachment],
        widget=forms.ClearableFileInput(attrs={"accept": FILE_ACCEPT}),
    )
    next_status = forms.ChoiceField(
        label="После отправки",
        choices=[
            ("in_progress", "Оставить в работе"),
            ("awaiting_resident", "Ждать ответ жителя"),
            ("resolved", "Это итоговый ответ — решить обращение"),
        ],
        initial="in_progress",
    )

def _require_view(request):
    if not request.user.is_staff or not can_any(request.user, "appeals.view"):
        raise PermissionDenied


def _can_reply(request, appeal=None):
    if appeal is not None:
        return can_for_appeal(request.user, "appeals.reply", appeal)
    return can_any(request.user, "appeals.reply")


def _can_close(request, appeal=None):
    if appeal is not None:
        return can_for_appeal(request.user, "appeals.close", appeal)
    return can_any(request.user, "appeals.close")


def appeal_list(request):
    _require_view(request)
    context = _base_context(request, section="appeals")
    state = request.GET.get("state") or "open"
    if state not in {"open", "new", "waiting", "resolved", "all"}:
        state = "open"
    q = " ".join((request.GET.get("q") or "").split())[:160]
    account_id = request.GET.get("account") or ""
    if not account_id.isdigit():
        account_id = ""

    count_base = scoped_appeals(request.user)
    base = count_base.select_related("account", "category")
    if account_id:
        account_pk = int(account_id)
        count_base = count_base.filter(account_id=account_pk)
        base = base.filter(account_id=account_pk)
    counts = count_base.aggregate(
        total=Count("id"),
        open=Count("id", filter=Q(status__in=OPEN_APPEAL_STATES)),
        new=Count("id", filter=Q(status="new")),
        waiting=Count("id", filter=Q(status="awaiting_resident")),
        resolved=Count("id", filter=Q(status__in=FINAL_APPEAL_STATES)),
    )

    if state == "open":
        base = base.filter(status__in=OPEN_APPEAL_STATES)
    elif state == "new":
        base = base.filter(status="new")
    elif state == "waiting":
        base = base.filter(status="awaiting_resident")
    elif state == "resolved":
        base = base.filter(status__in=FINAL_APPEAL_STATES)

    if q:
        criteria = (
            Q(subject__icontains=q)
            | Q(category__name__icontains=q)
            | Q(account__number__icontains=q)
            | Q(account__plot__icontains=q)
        )
        if q.isdigit():
            criteria |= Q(pk=int(q))
        base = base.filter(criteria)

    page = Paginator(base.order_by("-opened_at", "-id"), 30).get_page(request.GET.get("page"))
    context.update({
        "appeal_state": state,
        "q": q,
        "account_id": account_id,
        "counts": counts,
        "page": page,
        "can_manage_appeals": _can_reply(request) or _can_close(request),
    })
    return TemplateResponse(request, "water/work/appeals.html", context)


def appeal_detail(request, appeal_id):
    _require_view(request)
    appeal = get_object_or_404(
        scoped_appeals(request.user).select_related("account", "category", "responded_by"),
        pk=appeal_id,
    )
    can_reply = _can_reply(request, appeal)
    can_close = _can_close(request, appeal)
    form = StaffAppealReplyForm(request.POST or None, request.FILES or None)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "reply" and not can_reply:
            raise PermissionDenied
        if action == "close" and not can_close:
            raise PermissionDenied
        if action == "reply" and appeal.status in FINAL_APPEAL_STATES:
            messages.error(request, "Обращение уже завершено. Новое сообщение не отправлено.")
            return HttpResponseRedirect(reverse("staff_workspace:appeal", args=[appeal.pk]))
        if action == "close":
            try:
                close_resolved_appeal(appeal_id=appeal.pk, actor=request.user)
            except ValidationError as error:
                messages.error(request, "; ".join(error.messages))
            else:
                messages.success(request, "Обращение закрыто.")
            return HttpResponseRedirect(reverse("staff_workspace:appeal", args=[appeal.pk]))
        if action == "reply":
            valid = form.is_valid()
            if not valid:
                record_form_upload_rejection(
                    form=form,
                    field_name="document",
                    request=request,
                    actor=request.user,
                    account=appeal.account,
                    appeal=appeal,
                )
            else:
                try:
                    send_board_reply(
                        appeal_id=appeal.pk,
                        actor=request.user,
                        body=form.cleaned_data["body"],
                        document=form.cleaned_data.get("document"),
                        next_status=form.cleaned_data["next_status"],
                    )
                except ValidationError as error:
                    form.add_error(None, "; ".join(error.messages))
                else:
                    messages.success(request, "Сообщение отправлено жителю.")
                    return HttpResponseRedirect(reverse("staff_workspace:appeal", args=[appeal.pk]))

    appeal.refresh_from_db()
    context = _base_context(request, section="appeals")
    context.update({
        "appeal": appeal,
        "events": appeal_conversation_events(appeal),
        "form": form,
        "can_manage_appeals": can_reply or can_close,
        "can_reply": can_reply and appeal.status in OPEN_APPEAL_STATES,
        "can_close": can_close and appeal.status == "resolved",
    })
    return TemplateResponse(request, "water/work/appeal.html", context)


workspace_appeals = admin.site.admin_view(appeal_list)
workspace_appeal = admin.site.admin_view(appeal_detail)
workspace_appeal_attachment = admin.site.admin_view(download_appeal_attachment)
