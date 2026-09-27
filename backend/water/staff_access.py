from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from .access_requests import ResidentAccessRequest
from .access_workflow import (
    approve_access_request,
    end_resident_access,
    issue_access_password_reset,
    reject_access_request,
    revoke_invite,
    revoke_password_reset,
)
from .models import Account, ResidentAccess, ResidentInvite, ResidentPasswordReset
from .staff_workspace import _base_context


class AccessRequestApproveForm(forms.Form):
    account = forms.ModelChoiceField(label="Проверенный лицевой счёт", queryset=Account.objects.none())
    role = forms.ChoiceField(label="Основание доступа", choices=ResidentAccess._meta.get_field("role").choices)
    email = forms.EmailField(label="Email для одноразового приглашения")
    decision_note = forms.CharField(
        label="Основание решения",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(archived=False).order_by("plot", "number", "id")


class AccessRequestRejectForm(forms.Form):
    decision_note = forms.CharField(
        label="Причина отклонения",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
    )


class EndAccessForm(forms.Form):
    ends_on = forms.DateField(label="Завершить доступ с", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["ends_on"].initial = timezone.localdate()

    def clean_ends_on(self):
        value = self.cleaned_data["ends_on"]
        if self.access and value <= self.access.starts:
            raise forms.ValidationError("Дата завершения должна быть позже даты начала доступа.")
        return value


def _can(user, permission):
    return user.is_superuser or user.has_perm(permission)


def _can_review_requests(user):
    return user.is_superuser or (
        user.has_perm("water.access_private_registry")
        and user.has_perm("water.view_residentaccessrequest")
    )


def _can_change_requests(user):
    return user.is_superuser or (
        user.has_perm("water.access_private_registry")
        and user.has_perm("water.change_residentaccessrequest")
    )


def _can_manage_access(user):
    required = (
        "water.view_residentaccess",
        "water.view_residentinvite",
        "water.view_residentpasswordreset",
    )
    return user.is_superuser or all(user.has_perm(permission) for permission in required)


def _require_workspace(request):
    if not request.user.is_staff or not (
        _can_review_requests(request.user) or _can_manage_access(request.user)
    ):
        raise PermissionDenied


def _require_request_review(request):
    if not _can_review_requests(request.user):
        raise PermissionDenied


def _require_access_management(request):
    if not _can_manage_access(request.user):
        raise PermissionDenied


def access_dashboard(request):
    _require_workspace(request)
    context = _base_context(request, section="access")
    can_review = _can_review_requests(request.user)
    can_manage = _can_manage_access(request.user)
    q = " ".join((request.GET.get("q") or "").split())[:160]
    request_state = request.GET.get("request_state") or "new"
    if request_state not in {"new", "approved", "rejected", "all"}:
        request_state = "new"
    access_state = request.GET.get("access_state") or "active"
    if access_state not in {"active", "ended", "all"}:
        access_state = "active"

    context.update({
        "can_review_access_requests": can_review,
        "can_manage_access": can_manage,
        "can_approve_access_requests": can_review and _can(request.user, "water.add_residentinvite"),
        "q": q,
        "request_state": request_state,
        "access_state": access_state,
    })

    if can_review:
        requests = ResidentAccessRequest.objects.select_related("matched_account", "decided_by", "invite")
        if request_state != "all":
            requests = requests.filter(status=request_state)
        if q:
            requests = requests.filter(
                Q(full_name__icontains=q)
                | Q(email__icontains=q)
                | Q(phone__icontains=q)
                | Q(plot_hint__icontains=q)
            )
        context.update({
            "new_request_count": ResidentAccessRequest.objects.filter(status=ResidentAccessRequest.STATUS_NEW).count(),
            "access_requests": list(requests.order_by("status", "-submitted_at", "-id")[:40]),
        })

    if can_manage:
        today = timezone.localdate()
        now = timezone.now()
        accesses = ResidentAccess.objects.select_related("user", "account")
        if access_state == "active":
            accesses = accesses.filter(starts__lte=today).filter(Q(ends__isnull=True) | Q(ends__gt=today))
        elif access_state == "ended":
            accesses = accesses.filter(ends__lte=today)
        if q:
            accesses = accesses.filter(
                Q(user__username__icontains=q)
                | Q(user__email__icontains=q)
                | Q(account__number__icontains=q)
                | Q(account__plot__icontains=q)
            )
        context.update({
            "active_access_count": ResidentAccess.objects.filter(starts__lte=today)
            .filter(Q(ends__isnull=True) | Q(ends__gt=today)).count(),
            "accesses": list(accesses.order_by("ends", "account__plot", "account__number", "id")[:40]),
            "active_invites": list(
                ResidentInvite.objects.filter(used_at__isnull=True, revoked=False, expires_at__gt=now)
                .select_related("account")
                .order_by("expires_at", "id")[:30]
            ),
            "active_resets": list(
                ResidentPasswordReset.objects.filter(used_at__isnull=True, revoked=False, expires_at__gt=now)
                .select_related("user")
                .order_by("expires_at", "id")[:30]
            ),
            "can_revoke_invite": _can(request.user, "water.change_residentinvite"),
            "can_revoke_reset": _can(request.user, "water.change_residentpasswordreset"),
        })

    return TemplateResponse(request, "water/work/access/dashboard.html", context)


def request_detail(request, request_id):
    _require_request_review(request)
    request_obj = get_object_or_404(
        ResidentAccessRequest.objects.select_related("matched_account", "decided_by", "invite"),
        pk=request_id,
    )
    can_change = _can_change_requests(request.user)
    can_approve = can_change and _can(request.user, "water.add_residentinvite")
    approve_form = AccessRequestApproveForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "approve" else None,
        initial={"email": request_obj.email, "role": "owner"},
    )
    reject_form = AccessRequestRejectForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "reject" else None,
    )
    invite_url = None

    if request.method == "POST":
        action = request.POST.get("action") or ""
        if action == "approve":
            if not can_approve:
                raise PermissionDenied
            if approve_form.is_valid():
                try:
                    request_obj, _invite, raw = approve_access_request(
                        request_obj.pk,
                        account=approve_form.cleaned_data["account"],
                        email=approve_form.cleaned_data["email"],
                        role=approve_form.cleaned_data["role"],
                        decision_note=approve_form.cleaned_data["decision_note"],
                        actor=request.user,
                    )
                except ValidationError as error:
                    approve_form.add_error(None, "; ".join(error.messages))
                else:
                    invite_url = request.build_absolute_uri(reverse("resident_invite", args=[raw]))
                    messages.success(request, "Заявка одобрена. Одноразовое приглашение создано; доступ появится только после активации жителем.")
        elif action == "reject":
            if not can_change:
                raise PermissionDenied
            if reject_form.is_valid():
                try:
                    reject_access_request(
                        request_obj.pk,
                        decision_note=reject_form.cleaned_data["decision_note"],
                        actor=request.user,
                    )
                except ValidationError as error:
                    reject_form.add_error(None, "; ".join(error.messages))
                else:
                    messages.success(request, "Заявка отклонена. Решение зафиксировано.")
                    return HttpResponseRedirect(reverse("staff_workspace:access_request", args=[request_obj.pk]))
        else:
            messages.error(request, "Неизвестное действие с заявкой.")

    request_obj.refresh_from_db()
    context = _base_context(request, section="access")
    context.update({
        "request_obj": request_obj,
        "approve_form": approve_form,
        "reject_form": reject_form,
        "can_change_access_request": can_change,
        "can_approve_access_request": can_approve,
        "invite_url": invite_url,
    })
    return TemplateResponse(request, "water/work/access/request.html", context)


def access_detail(request, access_id):
    _require_access_management(request)
    access = get_object_or_404(ResidentAccess.objects.select_related("user", "account"), pk=access_id)
    end_form = EndAccessForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "end" else None,
        access=access,
    )
    reset_url = None

    if request.method == "POST":
        action = request.POST.get("action") or ""
        try:
            if action == "end":
                if not _can(request.user, "water.change_residentaccess"):
                    raise PermissionDenied
                if end_form.is_valid():
                    end_resident_access(
                        access.pk,
                        ends_on=end_form.cleaned_data["ends_on"],
                        actor=request.user,
                    )
                    messages.success(request, "Доступ завершён датой; запись и история сохранены.")
                    return HttpResponseRedirect(reverse("staff_workspace:access_detail", args=[access.pk]))
            elif action == "reset":
                if not _can(request.user, "water.add_residentpasswordreset"):
                    raise PermissionDenied
                _reset, raw = issue_access_password_reset(access.pk, actor=request.user)
                reset_url = request.build_absolute_uri(reverse("resident_password_reset", args=[raw]))
                messages.success(request, "Создана одноразовая ссылка восстановления. Она показывается только сейчас.")
            else:
                messages.error(request, "Неизвестное действие с доступом.")
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))

    access.refresh_from_db()
    context = _base_context(request, section="access")
    context.update({
        "access": access,
        "end_form": end_form,
        "reset_url": reset_url,
        "can_end_access": _can(request.user, "water.change_residentaccess") and access.ends is None,
        "can_issue_reset": _can(request.user, "water.add_residentpasswordreset") and access.ends is None,
        "resets": list(
            ResidentPasswordReset.objects.filter(user=access.user)
            .order_by("-id")[:12]
        ),
    })
    return TemplateResponse(request, "water/work/access/access.html", context)


def revoke_invite_view(request, invite_id):
    _require_access_management(request)
    if request.method != "POST" or not _can(request.user, "water.change_residentinvite"):
        raise PermissionDenied
    invite = get_object_or_404(ResidentInvite, pk=invite_id)
    try:
        revoke_invite(invite.pk, actor=request.user)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        messages.success(request, "Неиспользованное приглашение отозвано.")
    return HttpResponseRedirect(reverse("staff_workspace:access"))


def revoke_reset_view(request, reset_id):
    _require_access_management(request)
    if request.method != "POST" or not _can(request.user, "water.change_residentpasswordreset"):
        raise PermissionDenied
    reset = get_object_or_404(ResidentPasswordReset, pk=reset_id)
    try:
        revoke_password_reset(reset.pk, actor=request.user)
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        messages.success(request, "Ссылка восстановления отозвана.")
    return HttpResponseRedirect(reverse("staff_workspace:access"))


workspace_access = admin.site.admin_view(access_dashboard)
workspace_access_request = admin.site.admin_view(request_detail)
workspace_access_detail = admin.site.admin_view(access_detail)
workspace_access_revoke_invite = admin.site.admin_view(revoke_invite_view)
workspace_access_revoke_reset = admin.site.admin_view(revoke_reset_view)
