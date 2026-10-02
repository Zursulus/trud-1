from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from .access_requests import ResidentAccessRequest
from .appeal_workflow import OPEN_APPEAL_STATES, scoped_appeals
from .billing import account_totals
from .access_policy import ScopeType
from .access_resolver import can, can_any, scopes_for
from .access_scope import ScopeRef, scoped_accounts as accounts_in_scope, scoped_records, can_on_record
from .models import (
    Account,
    AccountDocument,
    BillingPeriod,
    Charge,
    ControllerReadingSubmission,
    LandPlot,
    Membership,
    Meter,
    Payment,
    Reading,
    ResidentAccess,
    ResidentAppeal,
)


def _group_ids_for_capability(user, capability, on_date=None):
    ids = set()
    for scope in scopes_for(user, capability, on_date=on_date):
        if scope.type == ScopeType.ALL:
            return list(WaterGroup.objects.values_list("id", flat=True))
        if scope.type == ScopeType.WATER_GROUP:
            ids.add(scope.object_id)
        elif scope.type == ScopeType.SUPPLY_NODE:
            ids.update(WaterGroup.objects.filter(node_id=scope.object_id).values_list("id", flat=True))
    return sorted(ids)


def _controller_group_ids(user, on_date=None):
    # "controller workspace" is the line-senior workflow, not independent controller capture.
    ids = set(_group_ids_for_capability(user, "water.line_submission.submit", on_date))
    ids.update(_group_ids_for_capability(user, "water.observation.review_line", on_date))
    return sorted(ids)


def _active_controller_account_ids(user, on_date=None):
    on_date = on_date or timezone.localdate()
    return Membership.objects.filter(
        group_id__in=_controller_group_ids(user, on_date),
        starts__lte=on_date,
        account__archived=False,
    ).filter(
        Q(ends__isnull=True) | Q(ends__gt=on_date),
    ).values("account_id")


def scoped_accounts(user):
    """Return only accounts covered by accounts.view scopes."""
    scopes = scopes_for(user, "accounts.view")
    if any(scope.type == ScopeType.ALL for scope in scopes):
        return Account.objects.all()
    ids = set()
    for scope in scopes:
        ids.update(accounts_in_scope(scope).values_list("id", flat=True))
    if not ids:
        raise PermissionDenied
    return Account.objects.filter(pk__in=ids)


def scoped_water_meters(user, on_date=None):
    """Return active meters covered by water.meters.view scopes."""
    on_date = on_date or timezone.localdate()
    meters = Meter.objects.filter(
        Q(commissioned_on__isnull=True) | Q(commissioned_on__lte=on_date),
    ).filter(
        Q(retired_on__isnull=True) | Q(retired_on__gte=on_date),
    )
    scopes = scopes_for(user, "water.meters.view", on_date=on_date)
    if any(scope.type == ScopeType.ALL for scope in scopes):
        return meters
    query = Q(pk__in=[])
    for scope in scopes:
        if scope.type == ScopeType.SUPPLY_NODE:
            query |= Q(node_id=scope.object_id)
        elif scope.type == ScopeType.WATER_GROUP:
            account_ids = accounts_in_scope(scope, on_date).values("id")
            query |= Q(group_id=scope.object_id) | Q(account_id__in=Subquery(account_ids))
        elif scope.type == ScopeType.ACCOUNT:
            query |= Q(account_id=scope.object_id)
    if not scopes:
        raise PermissionDenied
    return meters.filter(query).distinct()


def _capabilities(user):
    finance_workspace_permissions = (
        "water.view_account", "water.view_billingperiod", "water.view_charge",
        "water.view_payment", "water.view_paymentallocation",
    )
    can_review_access_requests = user.is_superuser or (
        user.has_perm("water.access_private_registry")
        and user.has_perm("water.view_residentaccessrequest")
    ) or can_any(user, "access.request.review")
    access_management_permissions = (
        "water.view_residentaccess", "water.view_residentinvite", "water.view_residentpasswordreset",
    )
    can_manage_access = user.is_superuser or all(
        user.has_perm(permission) for permission in access_management_permissions
    ) or can(user, "access.view", scope=ScopeRef(ScopeType.ALL))
    line_senior = can_any(user, "water.line_submission.submit") or can_any(user, "water.observation.review_line")
    return {
        "can_view_accounts": can_any(user, "accounts.view"),
        "can_view_land_plots": can_any(user, "plots.view"),
        "can_use_controller_workspace": line_senior,
        "can_view_water": can_any(user, "water.view"),
        "can_view_finance": can_any(user, "finance.view"),
        "can_use_finance_workspace": can_any(user, "finance.view") or (
            user.is_superuser or all(user.has_perm(permission) for permission in finance_workspace_permissions)
        ),
        "can_view_appeals": can_any(user, "appeals.view"),
        "can_view_account_documents": can_any(user, "documents.account.view"),
        "can_view_public_documents": can_any(user, "documents.public.view"),
        "can_view_news": can_any(user, "news.view"),
        "can_view_documents": (
            can_any(user, "documents.account.view")
            or can_any(user, "documents.public.view")
            or can_any(user, "news.view")
        ),
        "can_view_governance": can_any(user, "governance.board.view"),
        "can_view_security_alerts": can_any(user, "security.alert.view"),
        "can_view_registry": can_any(user, "registry.view"),
        "can_manage_finance_policy": can_any(user, "finance.policy.manage"),
        "can_view_reading_history": can_any(user, "water.reading.view"),
        "can_edit_accounts": can_any(user, "accounts.edit"),
        "can_edit_land_plots": can_any(user, "plots.edit"),
        "can_view_access": can_manage_access,
        "can_review_access_requests": can_review_access_requests,
        "can_manage_access": can_manage_access,
        "can_use_access_workspace": can_review_access_requests or can_manage_access,
    }


def _base_context(request, *, section):
    return {
        "workspace_section": section,
        "workspace_title": "Рабочая база",
        "admin_url": reverse("admin:index"),
        **_capabilities(request.user),
    }


def dashboard(request):
    context = _base_context(request, section="home")
    accounts = scoped_accounts(request.user) if context["can_view_accounts"] else Account.objects.none()
    context["account_count"] = accounts.count()

    attention = []
    if can_any(request.user, "water.observation.finalize"):
        count = ControllerReadingSubmission.objects.filter(status="pending").exclude(
            line_review_status=ControllerReadingSubmission.LINE_REVIEW_PENDING,
        ).count()
        if count:
            attention.append({
                "label": "Показания на финальной проверке",
                "count": count,
                "url": reverse("staff_workspace:water"),
            })
    elif context["can_use_controller_workspace"]:
        account_ids = _active_controller_account_ids(request.user)
        count = ControllerReadingSubmission.objects.filter(
            source=ControllerReadingSubmission.SOURCE_RESIDENT,
            status="pending",
            line_review_status=ControllerReadingSubmission.LINE_REVIEW_PENDING,
            meter__kind="individual",
            meter__account_id__in=Subquery(account_ids),
        ).count()
        if count:
            attention.append({
                "label": "Наблюдения жителей на сверке",
                "count": count,
                "url": reverse("staff_workspace:water"),
            })
    if context["can_view_appeals"]:
        count = scoped_appeals(request.user).filter(status__in=OPEN_APPEAL_STATES).count()
        if count:
            attention.append({
                "label": "Открытые обращения",
                "count": count,
                "url": reverse("staff_workspace:appeals") + "?state=open",
            })
    if context["can_use_finance_workspace"]:
        draft_count = scoped_records(Charge.objects.filter(status="draft"), request.user, "finance.view").count()
        if draft_count:
            attention.append({
                "label": "Черновики начислений на проверке",
                "count": draft_count,
                "url": reverse("staff_workspace:finance"),
            })
        pending_count = scoped_records(Payment.objects.filter(status="pending"), request.user, "finance.view").count()
        if pending_count:
            attention.append({
                "label": "Оплаты на проверке",
                "count": pending_count,
                "url": reverse("staff_workspace:finance_payments") + "?state=pending",
            })
        calculated_periods = scoped_records(BillingPeriod.objects.filter(status="calculated"), request.user,
                                           "finance.view", account_field="charge__account_id").distinct().count()
        if calculated_periods:
            attention.append({
                "label": "Рассчитанные периоды ждут завершения",
                "count": calculated_periods,
                "url": reverse("staff_workspace:finance"),
            })
    if context["can_review_access_requests"]:
        new_access_requests = ResidentAccessRequest.objects.filter(
            status=ResidentAccessRequest.STATUS_NEW,
        ).count()
        if new_access_requests:
            attention.append({
                "label": "Новые заявки на доступ",
                "count": new_access_requests,
                "url": reverse("staff_workspace:access") + "?request_state=new",
            })
    context["attention"] = attention
    return TemplateResponse(request, "water/work/dashboard.html", context)


def search(request):
    q = " ".join((request.GET.get("q") or "").split())[:160]
    context = _base_context(request, section="search")
    context["q"] = q
    results = []
    if q:
        criteria = Q(number__icontains=q) | Q(plot__icontains=q)
        if context["can_view_land_plots"]:
            criteria |= Q(land_plots__label__icontains=q) | Q(land_plots__address__icontains=q)
        if context["can_view_water"]:
            criteria |= Q(meter__serial__icontains=q)
        queryset = scoped_accounts(request.user).filter(criteria).distinct()
        if context["can_view_land_plots"]:
            queryset = queryset.prefetch_related("land_plots")
        results = list(queryset.order_by("archived", "plot", "number", "id")[:50])
    context["results"] = results
    return TemplateResponse(request, "water/work/search.html", context)


def account_list(request):
    context = _base_context(request, section="accounts")
    q = " ".join((request.GET.get("q") or "").split())[:160]
    accounts = scoped_accounts(request.user)
    if q:
        accounts = accounts.filter(Q(number__icontains=q) | Q(plot__icontains=q))
    if context["can_view_land_plots"]:
        accounts = accounts.prefetch_related("land_plots")
    accounts = accounts.order_by("archived", "plot", "number", "id")
    page = Paginator(accounts, 30).get_page(request.GET.get("page"))
    context.update({"q": q, "page": page})
    return TemplateResponse(request, "water/work/accounts.html", context)


def water_dashboard(request):
    context = _base_context(request, section="water")
    if not context["can_view_water"]:
        raise PermissionDenied

    today = timezone.localdate()
    meters = scoped_water_meters(request.user, today)
    meter_ids = meters.values("pk")
    balance_scopes = scopes_for(request.user, "water.balance.view", on_date=today)
    context.update({
        "meter_count": meters.count(),
        "today_reading_count": Reading.objects.filter(
            meter_id__in=Subquery(meter_ids), date=today,
        ).count(),
        "can_enter_readings": can_any(request.user, "water.reading.submit_official"),
        "can_capture_observation": can_any(request.user, "water.observation.submit"),
        "can_moderate_submissions": can_any(request.user, "water.observation.finalize"),
        "can_review_readings": can_any(request.user, "water.reading.view"),
        "can_view_readings_global": any(
            scope.type == ScopeType.ALL for scope in scopes_for(request.user, "water.reading.view", on_date=today)
        ),
        "can_view_meters": can_any(request.user, "water.meters.view"),
        "can_view_meters_global": any(
            scope.type == ScopeType.ALL for scope in scopes_for(request.user, "water.meters.view", on_date=today)
        ),
        "can_view_balance": any(scope.type in (ScopeType.ALL, ScopeType.SUPPLY_NODE) for scope in balance_scopes),
    })

    if context["can_use_controller_workspace"] and not context["can_moderate_submissions"]:
        account_ids = _active_controller_account_ids(request.user, today)
        line_review_qs = ControllerReadingSubmission.objects.filter(
            source=ControllerReadingSubmission.SOURCE_RESIDENT,
            status="pending",
            line_review_status=ControllerReadingSubmission.LINE_REVIEW_PENDING,
            meter__kind="individual",
            meter__account_id__in=Subquery(account_ids),
        ).select_related("meter", "meter__account").order_by("-submitted_at", "-id")
        context.update({
            "controller_line_count": len(_controller_group_ids(request.user, today)),
            "own_pending_count": ControllerReadingSubmission.objects.filter(
                submitted_by=request.user, status="pending",
            ).count(),
            "line_review_count": line_review_qs.count(),
            "line_review_items": list(line_review_qs[:12]),
        })
    elif context["can_moderate_submissions"]:
        pending = ControllerReadingSubmission.objects.filter(
            status="pending", meter_id__in=Subquery(meter_ids),
        )
        final_review_qs = pending.exclude(
            line_review_status=ControllerReadingSubmission.LINE_REVIEW_PENDING,
        ).select_related("meter", "meter__account").order_by("-submitted_at", "-id")
        context.update({
            "final_review_count": final_review_qs.count(),
            "line_review_waiting_count": pending.filter(
                line_review_status=ControllerReadingSubmission.LINE_REVIEW_PENDING,
            ).count(),
            "final_review_items": list(final_review_qs[:12]),
        })

    return TemplateResponse(request, "water/work/water.html", context)


def _meter_rows(user, account):
    capabilities = _capabilities(user)
    if not capabilities["can_view_water"]:
        return []
    latest = Reading.objects.filter(meter=OuterRef("pk")).order_by("-date", "-id")
    return list(
        Meter.objects.filter(account=account, kind="individual")
        .select_related("node", "group")
        .annotate(
            latest_reading_date=Subquery(latest.values("date")[:1]),
            latest_reading_value=Subquery(latest.values("value")[:1]),
        )
        .order_by("retired_on", "serial", "id")
    )


def account_detail(request, account_id):
    account = get_object_or_404(scoped_accounts(request.user), pk=account_id)
    context = _base_context(request, section="accounts")
    today = timezone.localdate()

    memberships = []
    if context["can_view_water"]:
        membership_qs = Membership.objects.filter(
            account=account,
            starts__lte=today,
        ).filter(
            Q(ends__isnull=True) | Q(ends__gt=today)
        ).select_related("group", "group__node")
        if not can(request.user, "water.topology.view", scope=ScopeRef(ScopeType.ACCOUNT, account.pk), on_date=today):
            membership_qs = membership_qs.none()
        elif not any(scope.type == ScopeType.ALL for scope in scopes_for(request.user, "water.topology.view", on_date=today)):
            membership_qs = membership_qs.filter(group_id__in=_group_ids_for_capability(request.user, "water.topology.view", today))
        memberships = list(membership_qs)

    land_plots = []
    if context["can_view_land_plots"]:
        land_plots = list(
            LandPlot.objects.filter(account=account).only(
                "id", "label", "address", "archived", "account_id"
            ).order_by("archived", "label", "id")
        )

    context.update({
        "account": account,
        "land_plots": land_plots,
        "memberships": memberships,
        "meters": _meter_rows(request.user, account),
    })

    context["can_view_account_finance"] = can_on_record(request.user, "finance.view", account_id=account.pk)
    if context["can_view_account_finance"]:
        context["finance"] = account_totals(account)
    context["can_view_account_appeals"] = can_on_record(request.user, "appeals.view", account_id=account.pk)
    if context["can_view_account_appeals"]:
        context["open_appeals"] = scoped_appeals(request.user).filter(
            account=account, status__in=OPEN_APPEAL_STATES
        ).count()
    context["can_view_account_documents"] = can_on_record(request.user, "documents.account.view", account_id=account.pk)
    if context["can_view_account_documents"]:
        context["documents_count"] = AccountDocument.objects.filter(account=account).count()
    if context["can_view_access"]:
        context["access_count"] = ResidentAccess.objects.filter(
            account=account,
            starts__lte=today,
        ).filter(Q(ends__isnull=True) | Q(ends__gt=today)).count()

    return TemplateResponse(request, "water/work/account.html", context)


# Every /work/ route inherits the existing staff login/security gate.
workspace_dashboard = admin.site.admin_view(dashboard)
workspace_search = admin.site.admin_view(search)
workspace_accounts = admin.site.admin_view(account_list)
workspace_water = admin.site.admin_view(water_dashboard)
workspace_account = admin.site.admin_view(account_detail)
