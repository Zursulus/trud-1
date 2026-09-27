from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import OuterRef, Q, Subquery
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from .billing import account_totals
from .controller_scope import ControllerLineAccess
from .models import (
    Account,
    AccountDocument,
    ControllerReadingSubmission,
    LandPlot,
    Membership,
    Meter,
    Reading,
    ResidentAccess,
    ResidentAppeal,
)

OPEN_APPEAL_STATES = ("new", "in_progress", "awaiting_resident")


def _controller_group_ids(user, on_date=None):
    on_date = on_date or timezone.localdate()
    return ControllerLineAccess.objects.filter(
        user=user,
        starts__lte=on_date,
    ).filter(
        Q(ends__isnull=True) | Q(ends__gt=on_date),
    ).values_list("group_id", flat=True)


def scoped_accounts(user):
    """Return only accounts the current staff user may inspect in /work/."""
    if user.is_superuser or user.has_perm("water.view_account"):
        return Account.objects.all()
    if user.has_perm("water.use_controller_workspace"):
        group_ids = _controller_group_ids(user)
        account_ids = Membership.objects.filter(
            group_id__in=group_ids,
            starts__lte=timezone.localdate(),
        ).filter(
            Q(ends__isnull=True) | Q(ends__gt=timezone.localdate()),
        ).values("account_id")
        return Account.objects.filter(pk__in=Subquery(account_ids))
    raise PermissionDenied


def _base_context(request, *, section):
    user = request.user
    return {
        "workspace_section": section,
        "workspace_title": "Рабочая база",
        "can_view_accounts": user.is_superuser
        or user.has_perm("water.view_account")
        or user.has_perm("water.use_controller_workspace"),
        "can_use_controller_workspace": user.has_perm("water.use_controller_workspace"),
        "can_view_water": user.has_perm("water.view_meter")
        or user.has_perm("water.use_controller_workspace"),
        "can_view_finance": user.has_perm("water.view_charge")
        and user.has_perm("water.view_payment"),
        "can_view_appeals": user.has_perm("water.view_residentappeal"),
        "can_view_documents": user.has_perm("water.view_accountdocument"),
        "can_view_access": user.has_perm("water.view_residentaccess"),
        "admin_url": reverse("admin:index"),
    }


def dashboard(request):
    accounts = scoped_accounts(request.user)
    context = _base_context(request, section="home")
    context["account_count"] = accounts.count()

    attention = []
    if request.user.has_perm("water.change_controllerreadingsubmission"):
        count = ControllerReadingSubmission.objects.filter(status="pending").count()
        if count:
            attention.append({
                "label": "Показания на проверке",
                "count": count,
                "url": reverse("admin:water_controllerreadingsubmission_changelist")
                + "?status__exact=pending",
            })
    if context["can_view_appeals"]:
        count = ResidentAppeal.objects.filter(status__in=OPEN_APPEAL_STATES).count()
        if count:
            attention.append({
                "label": "Открытые обращения",
                "count": count,
                "url": reverse("admin:water_residentappeal_changelist"),
            })
    context["attention"] = attention
    return TemplateResponse(request, "water/work/dashboard.html", context)


def search(request):
    q = " ".join((request.GET.get("q") or "").split())[:160]
    context = _base_context(request, section="search")
    context["q"] = q
    results = []
    if q:
        results = list(
            scoped_accounts(request.user)
            .filter(
                Q(number__icontains=q)
                | Q(plot__icontains=q)
                | Q(land_plots__label__icontains=q)
                | Q(land_plots__address__icontains=q)
                | Q(meter__serial__icontains=q)
            )
            .distinct()
            .prefetch_related("land_plots")
            .order_by("archived", "plot", "number", "id")[:50]
        )
    context["results"] = results
    return TemplateResponse(request, "water/work/search.html", context)


def account_list(request):
    context = _base_context(request, section="accounts")
    q = " ".join((request.GET.get("q") or "").split())[:160]
    accounts = scoped_accounts(request.user)
    if q:
        accounts = accounts.filter(Q(number__icontains=q) | Q(plot__icontains=q))
    accounts = accounts.prefetch_related("land_plots").order_by("archived", "plot", "number", "id")
    page = Paginator(accounts, 30).get_page(request.GET.get("page"))
    context.update({"q": q, "page": page})
    return TemplateResponse(request, "water/work/accounts.html", context)


def _meter_rows(user, account):
    if not (user.has_perm("water.view_meter") or user.has_perm("water.use_controller_workspace")):
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
    accounts = scoped_accounts(request.user)
    account = get_object_or_404(accounts, pk=account_id)
    context = _base_context(request, section="accounts")

    today = timezone.localdate()
    memberships = Membership.objects.filter(
        account=account,
        starts__lte=today,
    ).filter(Q(ends__isnull=True) | Q(ends__gt=today)).select_related("group", "group__node")

    if not (request.user.is_superuser or request.user.has_perm("water.view_account")):
        memberships = memberships.filter(group_id__in=_controller_group_ids(request.user, today))

    land_plots = list(
        LandPlot.objects.filter(account=account).only(
            "id", "label", "address", "archived", "account_id"
        ).order_by("archived", "label", "id")
    )

    context.update({
        "account": account,
        "land_plots": land_plots,
        "memberships": list(memberships),
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


# Export wrapped views so every /work/ route inherits the existing staff login/security gate.
workspace_dashboard = admin.site.admin_view(dashboard)
workspace_search = admin.site.admin_view(search)
workspace_accounts = admin.site.admin_view(account_list)
workspace_account = admin.site.admin_view(account_detail)
