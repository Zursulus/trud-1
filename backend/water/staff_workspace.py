from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import OuterRef, Q, Subquery
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from .appeal_workflow import OPEN_APPEAL_STATES
from .billing import account_totals
from .controller_scope import ControllerLineAccess
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


def _controller_group_ids(user, on_date=None):
    on_date = on_date or timezone.localdate()
    return ControllerLineAccess.objects.filter(
        user=user,
        starts__lte=on_date,
    ).filter(
        Q(ends__isnull=True) | Q(ends__gt=on_date),
    ).values_list("group_id", flat=True)


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
    """Return only accounts the current staff user may inspect in /work/."""
    if user.is_superuser or user.has_perm("water.view_account"):
        return Account.objects.all()
    if user.has_perm("water.use_controller_workspace"):
        return Account.objects.filter(pk__in=Subquery(_active_controller_account_ids(user)))
    raise PermissionDenied


def scoped_water_meters(user, on_date=None):
    """Return active meters visible to this staff role without widening its scope."""
    on_date = on_date or timezone.localdate()
    meters = Meter.objects.filter(
        Q(commissioned_on__isnull=True) | Q(commissioned_on__lte=on_date),
    ).filter(
        Q(retired_on__isnull=True) | Q(retired_on__gte=on_date),
    )
    if user.is_superuser or user.has_perm("water.view_meter"):
        return meters
    if user.has_perm("water.use_controller_workspace"):
        group_ids = _controller_group_ids(user, on_date)
        account_ids = _active_controller_account_ids(user, on_date)
        return meters.filter(
            Q(kind="line", group_id__in=group_ids)
            | Q(kind="individual", account_id__in=Subquery(account_ids))
        )
    raise PermissionDenied


def _capabilities(user):
    finance_workspace_permissions = (
        "water.view_account",
        "water.view_billingperiod",
        "water.view_charge",
        "water.view_payment",
        "water.view_paymentallocation",
    )
    return {
        "can_view_accounts": user.is_superuser
        or user.has_perm("water.view_account")
        or user.has_perm("water.use_controller_workspace"),
        "can_view_land_plots": user.is_superuser or user.has_perm("water.view_landplot"),
        "can_use_controller_workspace": user.has_perm("water.use_controller_workspace"),
        "can_view_water": user.is_superuser
        or user.has_perm("water.view_meter")
        or user.has_perm("water.use_controller_workspace"),
        "can_view_finance": user.is_superuser
        or (user.has_perm("water.view_charge") and user.has_perm("water.view_payment")),
        "can_use_finance_workspace": user.is_superuser
        or all(user.has_perm(permission) for permission in finance_workspace_permissions),
        "can_view_appeals": user.is_superuser or user.has_perm("water.view_residentappeal"),
        "can_view_documents": user.is_superuser or user.has_perm("water.view_accountdocument"),
        "can_view_access": user.is_superuser or user.has_perm("water.view_residentaccess"),
    }


def _base_context(request, *, section):
    return {
        "workspace_section": section,
        "workspace_title": "Рабочая база",
        "admin_url": reverse("admin:index"),
        **_capabilities(request.user),
    }


def dashboard(request):
    accounts = scoped_accounts(request.user)
    context = _base_context(request, section="home")
    context["account_count"] = accounts.count()

    attention = []
    if request.user.has_perm("water.change_controllerreadingsubmission"):
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
        count = ResidentAppeal.objects.filter(status__in=OPEN_APPEAL_STATES).count()
        if count:
            attention.append({
                "label": "Открытые обращения",
                "count": count,
                "url": reverse("staff_workspace:appeals") + "?state=open",
            })
    if context["can_use_finance_workspace"]:
        draft_count = Charge.objects.filter(status="draft").count()
        if draft_count:
            attention.append({
                "label": "Черновики начислений на проверке",
                "count": draft_count,
                "url": reverse("staff_workspace:finance"),
            })
        pending_count = Payment.objects.filter(status="pending").count()
        if pending_count:
            attention.append({
                "label": "Оплаты на проверке",
                "count": pending_count,
                "url": reverse("staff_workspace:finance_payments") + "?state=pending",
            })
        calculated_periods = BillingPeriod.objects.filter(status="calculated").count()
        if calculated_periods:
            attention.append({
                "label": "Рассчитанные периоды ждут завершения",
                "count": calculated_periods,
                "url": reverse("staff_workspace:finance"),
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
    context.update({
        "meter_count": meters.count(),
        "today_reading_count": Reading.objects.filter(
            meter_id__in=Subquery(meter_ids), date=today,
        ).count(),
        "can_enter_readings": request.user.is_superuser or request.user.has_perm("water.add_reading"),
        "can_capture_observation": request.user.is_superuser
        or request.user.has_perm("water.add_controllerreadingsubmission"),
        "can_moderate_submissions": request.user.is_superuser
        or request.user.has_perm("water.change_controllerreadingsubmission"),
        "can_review_readings": request.user.is_superuser or request.user.has_perm("water.view_reading"),
        "can_view_meters": request.user.is_superuser or request.user.has_perm("water.view_meter"),
        "can_view_balance": request.user.is_superuser or all(
            request.user.has_perm(permission)
            for permission in ("water.view_reading", "water.view_meter", "water.view_watergroup")
        ),
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
            "controller_line_count": _controller_group_ids(request.user, today).count(),
            "own_pending_count": ControllerReadingSubmission.objects.filter(
                submitted_by=request.user, status="pending",
            ).count(),
            "line_review_count": line_review_qs.count(),
            "line_review_items": list(line_review_qs[:12]),
        })
    elif context["can_moderate_submissions"]:
        pending = ControllerReadingSubmission.objects.filter(status="pending")
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
        if not (request.user.is_superuser or request.user.has_perm("water.view_membership")):
            membership_qs = membership_qs.filter(group_id__in=_controller_group_ids(request.user, today))
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

    if context["can_view_finance"]:
        context["finance"] = account_totals(account)
    if context["can_view_appeals"]:
        context["open_appeals"] = ResidentAppeal.objects.filter(
            account=account, status__in=OPEN_APPEAL_STATES
        ).count()
    if context["can_view_documents"]:
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
