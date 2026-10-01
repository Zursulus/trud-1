from urllib.parse import urlencode

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
from .access_resolver import can
from .access_policy import ScopeType
from .access_scope import ScopeRef


from .access_workflow import (
    approve_access_request,
    end_portal_grant,
    end_resident_access,
    issue_access_password_reset,
    issue_grant_password_reset,
    reject_access_request,
    revoke_invite,
    revoke_password_reset,
    update_portal_grant_rights,
)
from .models import Account, Person, ResidentAccess, ResidentInvite, ResidentPasswordReset
from .portal import issue_granular_invite
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity
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


class PersonCreateForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ("full_name", "email", "phone", "notes")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip().lower()
        if email and Person.objects.filter(email__iexact=email, archived=False).exists():
            raise forms.ValidationError("Человек с таким email уже есть в закрытом реестре. Выберите существующую карточку.")
        return email


class GranularInviteForm(forms.Form):
    person = forms.ModelChoiceField(label="Житель", queryset=Person.objects.none())
    account = forms.ModelChoiceField(label="Лицевой счёт", queryset=Account.objects.none())
    email = forms.EmailField(label="Email для одноразового приглашения")
    basis = forms.CharField(
        label="Проверенное основание", max_length=300,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    can_view_account = forms.BooleanField(label="Видеть участок и базовые данные", required=False, initial=True)
    can_view_finance = forms.BooleanField(label="Видеть начисления и оплаты", required=False)
    can_submit_water = forms.BooleanField(label="Передавать показания воды", required=False)
    can_view_documents = forms.BooleanField(label="Видеть документы лицевого счёта", required=False)
    can_use_appeals = forms.BooleanField(label="Создавать и читать свои обращения", required=False)
    can_represent = forms.BooleanField(label="Совершать представительские действия", required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["person"].queryset = Person.objects.filter(archived=False).order_by("full_name", "id")
        self.fields["account"].queryset = Account.objects.filter(archived=False).order_by("plot", "number", "id")

    def clean(self):
        data = super().clean()
        if any(data.get(name) for name in (
            "can_view_finance", "can_submit_water", "can_view_documents", "can_use_appeals", "can_represent",
        )) and not data.get("can_view_account"):
            self.add_error("can_view_account", "Специальные права требуют базового доступа к участку.")
        person = data.get("person")
        account = data.get("account")
        if person and account:
            today = timezone.localdate()
            if PortalGrant.objects.filter(person=person, account=account, starts__lte=today).filter(
                Q(ends__isnull=True) | Q(ends__gt=today)
            ).exists():
                self.add_error("account", "Для этого жителя уже действует явный доступ к выбранному счёту.")
        return data


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


class PortalGrantRightsForm(forms.Form):
    can_view_account = forms.BooleanField(label="Видеть участок и базовые данные", required=False)
    can_view_finance = forms.BooleanField(label="Видеть начисления и оплаты", required=False)
    can_submit_water = forms.BooleanField(label="Передавать показания воды", required=False)
    can_view_documents = forms.BooleanField(label="Видеть документы лицевого счёта", required=False)
    can_use_appeals = forms.BooleanField(label="Создавать и читать свои обращения", required=False)
    can_represent = forms.BooleanField(label="Совершать представительские действия", required=False)

    def clean(self):
        data = super().clean()
        if any(data.get(name) for name in (
            "can_view_finance", "can_submit_water", "can_view_documents",
            "can_use_appeals", "can_represent",
        )) and not data.get("can_view_account"):
            self.add_error("can_view_account", "Специальные права требуют базового доступа к участку.")
        return data


def _can_global(user, capability):
    # This center exposes whole-logins and shared access history. Scoped
    # authority must not pass a global UI gate (same as the V2 center).
    return can(user, capability, scope=ScopeRef(ScopeType.ALL))


def _can(user, permission):
    return user.is_superuser or user.has_perm(permission)


def _can_review_requests(user):
    return user.is_superuser or _can_global(user, "access.request.review") or (
        user.has_perm("water.access_private_registry")
        and user.has_perm("water.view_residentaccessrequest")
    )


def _can_change_requests(user):
    return user.is_superuser or _can_global(user, "access.request.decide") or (
        user.has_perm("water.access_private_registry")
        and user.has_perm("water.change_residentaccessrequest")
    )


def _can_manage_access(user):
    required = (
        "water.view_residentaccess",
        "water.view_residentinvite",
        "water.view_residentpasswordreset",
    )
    return user.is_superuser or _can_global(user, "access.view") or all(
        user.has_perm(permission) for permission in required
    )


def _can_issue_granular_invite(user):
    return _can_global(user, "access.invite.issue") or (
        _can_review_requests(user) and _can(user, "water.add_residentinvite")
    )


def _can_manage_grants(user):
    return user.is_superuser or _can_global(user, "access.grant.view") or (
        _can_manage_access(user) and user.has_perm("water.view_portalgrant")
    )


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
        "can_approve_access_requests": can_review and (_can(request.user, "water.add_residentinvite") or _can_global(request.user, "access.invite.issue")),
        "can_issue_granular_invite": _can_issue_granular_invite(request.user),
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
        grants = PortalGrant.objects.select_related("account", "person") if _can_manage_grants(request.user) else PortalGrant.objects.none()
        if access_state == "active":
            accesses = accesses.filter(starts__lte=today).filter(Q(ends__isnull=True) | Q(ends__gt=today))
            grants = grants.filter(starts__lte=today).filter(Q(ends__isnull=True) | Q(ends__gt=today))
        elif access_state == "ended":
            accesses = accesses.filter(ends__lte=today)
            grants = grants.filter(ends__lte=today)
        if q:
            accesses = accesses.filter(
                Q(user__username__icontains=q)
                | Q(user__email__icontains=q)
                | Q(account__number__icontains=q)
                | Q(account__plot__icontains=q)
            )
            grant_filter = Q(account__number__icontains=q) | Q(account__plot__icontains=q)
            if can_review:
                grant_filter |= Q(person__full_name__icontains=q) | Q(person__email__icontains=q)
            grants = grants.filter(grant_filter)
        active_legacy_count = ResidentAccess.objects.filter(starts__lte=today).filter(
            Q(ends__isnull=True) | Q(ends__gt=today)
        ).count()
        active_grant_count = 0
        if _can_manage_grants(request.user):
            active_grant_count = PortalGrant.objects.filter(starts__lte=today).filter(
                Q(ends__isnull=True) | Q(ends__gt=today)
            ).count()
        context.update({
            "active_access_count": active_legacy_count + active_grant_count,
            "accesses": list(accesses.order_by("ends", "account__plot", "account__number", "id")[:40]),
            "grants": list(grants.order_by("ends", "account__plot", "account__number", "id")[:40]),
            "can_see_grant_person": can_review,
            "active_invites": list(
                ResidentInvite.objects.filter(used_at__isnull=True, revoked=False, expires_at__gt=now)
                .select_related("account", "person")
                .order_by("expires_at", "id")[:30]
            ),
            "active_resets": list(
                ResidentPasswordReset.objects.filter(used_at__isnull=True, revoked=False, expires_at__gt=now)
                .select_related("user")
                .order_by("expires_at", "id")[:30]
            ),
            "can_revoke_invite": _can(request.user, "water.change_residentinvite") or _can_global(request.user, "access.invite.revoke"),
            "can_revoke_reset": _can(request.user, "water.change_residentpasswordreset") or _can_global(request.user, "access.password_reset.revoke"),
        })

    return TemplateResponse(request, "water/work/access/dashboard.html", context)


def person_create_for_access(request):
    if not _can_issue_granular_invite(request.user) or not (
        _can(request.user, "water.add_person") or _can_global(request.user, "access.person.create")
    ):
        raise PermissionDenied
    form = PersonCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        person = form.save(commit=False)
        person._history_user = request.user
        person._change_reason = "Житель добавлен из процесса выдачи доступа"
        person.save()
        messages.success(request, "Карточка жителя создана. Теперь выберите счёт и права.")
        query = urlencode({"person": person.pk, "email": person.email or ""})
        return HttpResponseRedirect(f'{reverse("staff_workspace:access_invite")}?{query}')
    context = _base_context(request, section="access")
    context.update({"form": form})
    return TemplateResponse(request, "water/work/access/person.html", context)


def granular_invite_create(request):
    if not _can_issue_granular_invite(request.user):
        raise PermissionDenied
    initial = {}
    if request.method != "POST":
        person_id = request.GET.get("person")
        if person_id and Person.objects.filter(pk=person_id, archived=False).exists():
            initial["person"] = person_id
        if request.GET.get("email"):
            initial["email"] = request.GET.get("email")
    form = GranularInviteForm(request.POST or None, initial=initial)
    invite_url = None
    invite = None
    if request.method == "POST" and form.is_valid():
        try:
            invite, raw = issue_granular_invite(
                form.cleaned_data["account"],
                form.cleaned_data["person"],
                form.cleaned_data["email"],
                form.cleaned_data["basis"],
                actor=request.user,
                can_view_account=form.cleaned_data["can_view_account"],
                can_view_finance=form.cleaned_data["can_view_finance"],
                can_submit_water=form.cleaned_data["can_submit_water"],
                can_view_documents=form.cleaned_data["can_view_documents"],
                can_use_appeals=form.cleaned_data["can_use_appeals"],
                can_represent=form.cleaned_data["can_represent"],
            )
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
        else:
            invite_url = request.build_absolute_uri(reverse("resident_invite", args=[raw]))
            messages.success(
                request,
                "Одноразовое приглашение создано. Права появятся только после активации жителем.",
            )
    context = _base_context(request, section="access")
    context.update({"form": form, "invite_url": invite_url, "invite": invite})
    return TemplateResponse(request, "water/work/access/invite.html", context)


def grant_detail(request, grant_id):
    _require_access_management(request)
    if not _can_manage_grants(request.user):
        raise PermissionDenied
    grant = get_object_or_404(PortalGrant.objects.select_related("person", "account", "verified_by"), pk=grant_id)
    identity = ResidentIdentity.objects.select_related("user").filter(person=grant.person).first()
    end_form = EndAccessForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "end" else None,
        access=grant,
    )
    rights_form = PortalGrantRightsForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "rights" else None,
        initial={
            "can_view_account": grant.can_view_account,
            "can_view_finance": grant.can_view_finance,
            "can_submit_water": grant.can_submit_water,
            "can_view_documents": grant.can_view_documents,
            "can_use_appeals": grant.can_use_appeals,
            "can_represent": grant.can_represent,
        },
    )
    reset_url = None
    if request.method == "POST":
        action = request.POST.get("action") or ""
        try:
            if action == "rights":
                if not (_can(request.user, "water.change_portalgrant") or _can_global(request.user, "access.grant.edit")):
                    raise PermissionDenied
                if rights_form.is_valid():
                    update_portal_grant_rights(
                        grant.pk, actor=request.user, **rights_form.cleaned_data
                    )
                    messages.success(request, "Набор личных прав обновлён; предыдущая версия сохранена в истории.")
                    return HttpResponseRedirect(reverse("staff_workspace:grant_detail", args=[grant.pk]))
            elif action == "end":
                if not (_can(request.user, "water.change_portalgrant") or _can_global(request.user, "access.grant.end")):
                    raise PermissionDenied
                if end_form.is_valid():
                    end_portal_grant(grant.pk, ends_on=end_form.cleaned_data["ends_on"], actor=request.user)
                    messages.success(request, "Доступ завершён датой; запись и история сохранены.")
                    return HttpResponseRedirect(reverse("staff_workspace:grant_detail", args=[grant.pk]))
            elif action == "reset":
                if not (_can(request.user, "water.add_residentpasswordreset") or _can_global(request.user, "access.password_reset.issue")):
                    raise PermissionDenied
                _reset, raw = issue_grant_password_reset(grant.pk, actor=request.user)
                reset_url = request.build_absolute_uri(reverse("resident_password_reset", args=[raw]))
                messages.success(request, "Создана одноразовая ссылка восстановления. Она показывается только сейчас.")
            else:
                messages.error(request, "Неизвестное действие с доступом.")
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
    grant.refresh_from_db()
    identity = ResidentIdentity.objects.select_related("user").filter(person=grant.person).first()
    context = _base_context(request, section="access")
    context.update({
        "grant": grant,
        "identity": identity,
        "end_form": end_form,
        "rights_form": rights_form,
        "reset_url": reset_url,
        "can_see_person": _can_review_requests(request.user),
        "can_edit_rights": (_can(request.user, "water.change_portalgrant") or _can_global(request.user, "access.grant.edit")) and grant.ends is None,
        "can_end_access": (_can(request.user, "water.change_portalgrant") or _can_global(request.user, "access.grant.end")) and grant.ends is None,
        "can_issue_reset": bool(identity) and (_can(request.user, "water.add_residentpasswordreset") or _can_global(request.user, "access.password_reset.issue")) and grant.ends is None,
        "resets": list(
            ResidentPasswordReset.objects.filter(user=identity.user).order_by("-id")[:12]
        ) if identity else [],
    })
    return TemplateResponse(request, "water/work/access/grant.html", context)


def request_detail(request, request_id):
    _require_request_review(request)
    request_obj = get_object_or_404(
        ResidentAccessRequest.objects.select_related("matched_account", "decided_by", "invite"),
        pk=request_id,
    )
    can_change = _can_change_requests(request.user)
    can_approve = can_change and (_can(request.user, "water.add_residentinvite") or _can_global(request.user, "access.invite.issue"))
    approve_form = AccessRequestApproveForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "approve" else None,
        initial={"email": request_obj.email, "role": "owner"},
        prefix="approve",
    )
    reject_form = AccessRequestRejectForm(
        request.POST if request.method == "POST" and request.POST.get("action") == "reject" else None,
        prefix="reject",
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
                if not (_can(request.user, "water.change_residentaccess") or _can_global(request.user, "access.grant.end")):
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
                if not (_can(request.user, "water.add_residentpasswordreset") or _can_global(request.user, "access.password_reset.issue")):
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
        "can_end_access": (_can(request.user, "water.change_residentaccess") or _can_global(request.user, "access.grant.end")) and access.ends is None,
        "can_issue_reset": (_can(request.user, "water.add_residentpasswordreset") or _can_global(request.user, "access.password_reset.issue")) and access.ends is None,
        "resets": list(
            ResidentPasswordReset.objects.filter(user=access.user)
            .order_by("-id")[:12]
        ),
    })
    return TemplateResponse(request, "water/work/access/access.html", context)


def revoke_invite_view(request, invite_id):
    _require_access_management(request)
    if request.method != "POST" or not (_can(request.user, "water.change_residentinvite") or _can_global(request.user, "access.invite.revoke")):
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
    if request.method != "POST" or not (_can(request.user, "water.change_residentpasswordreset") or _can_global(request.user, "access.password_reset.revoke")):
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
workspace_access_person_create = admin.site.admin_view(person_create_for_access)
workspace_access_invite = admin.site.admin_view(granular_invite_create)
workspace_access_request = admin.site.admin_view(request_detail)
workspace_access_detail = admin.site.admin_view(access_detail)
workspace_grant_detail = admin.site.admin_view(grant_detail)
workspace_access_revoke_invite = admin.site.admin_view(revoke_invite_view)
workspace_access_revoke_reset = admin.site.admin_view(revoke_reset_view)
