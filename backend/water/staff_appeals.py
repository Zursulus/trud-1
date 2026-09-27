from pathlib import Path

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse

from .appeal_admin_tools import download_appeal_attachment
from .appeal_workflow import (
    FINAL_APPEAL_STATES,
    OPEN_APPEAL_STATES,
    appeal_conversation_events,
    close_resolved_appeal,
    send_board_reply,
)
from .models import ResidentAppeal
from .resident_models import APPEAL_ATTACHMENT_EXTENSIONS, APPEAL_ATTACHMENT_MAX_BYTES
from .staff_workspace import _base_context


class StaffAppealReplyForm(forms.Form):
    body = forms.CharField(
        label="Сообщение жителю",
        max_length=5000,
        widget=forms.Textarea(attrs={"rows": 6}),
    )
    document = forms.FileField(
        label="Вложение",
        required=False,
        help_text="Необязательно. PDF, JPG или PNG до 10 МБ.",
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

    def clean_document(self):
        upload = self.cleaned_data.get("document")
        if not upload:
            return upload
        if upload.size > APPEAL_ATTACHMENT_MAX_BYTES:
            raise forms.ValidationError("Файл должен быть не больше 10 МБ.")
        if Path(upload.name).suffix.lower() not in APPEAL_ATTACHMENT_EXTENSIONS:
            raise forms.ValidationError("Разрешены только PDF, JPG и PNG.")
        return upload


def _require_view(request):
    if not (request.user.is_superuser or request.user.has_perm("water.view_residentappeal")):
        raise PermissionDenied


def _can_change(request):
    return request.user.is_superuser or request.user.has_perm("water.change_residentappeal")


def appeal_list(request):
    _require_view(request)
    context = _base_context(request, section="appeals")
    state = request.GET.get("state") or "open"
    if state not in {"open", "new", "waiting", "resolved", "all"}:
        state = "open"
    q = " ".join((request.GET.get("q") or "").split())[:160]
    account_id = request.GET.get("account") or ""

    base = ResidentAppeal.objects.select_related("account", "category")
    counts = ResidentAppeal.objects.aggregate(
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

    if account_id.isdigit():
        base = base.filter(account_id=int(account_id))
    else:
        account_id = ""
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
        "can_manage_appeals": _can_change(request),
    })
    return TemplateResponse(request, "water/work/appeals.html", context)


def appeal_detail(request, appeal_id):
    _require_view(request)
    appeal = get_object_or_404(
        ResidentAppeal.objects.select_related("account", "category", "responded_by"),
        pk=appeal_id,
    )
    can_change = _can_change(request)
    form = StaffAppealReplyForm(request.POST or None, request.FILES or None)

    if request.method == "POST":
        if not can_change:
            raise PermissionDenied
        action = request.POST.get("action")
        if action == "close":
            try:
                close_resolved_appeal(appeal_id=appeal.pk, actor=request.user)
            except ValidationError as error:
                messages.error(request, "; ".join(error.messages))
            else:
                messages.success(request, "Обращение закрыто.")
            return HttpResponseRedirect(reverse("staff_workspace:appeal", args=[appeal.pk]))
        if action == "reply" and form.is_valid():
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
        "can_manage_appeals": can_change,
        "can_reply": can_change and appeal.status in OPEN_APPEAL_STATES,
        "can_close": can_change and appeal.status == "resolved",
    })
    return TemplateResponse(request, "water/work/appeal.html", context)


workspace_appeals = admin.site.admin_view(appeal_list)
workspace_appeal = admin.site.admin_view(appeal_detail)
workspace_appeal_attachment = admin.site.admin_view(download_appeal_attachment)
