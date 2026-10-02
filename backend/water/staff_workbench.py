"""Read-only administrative overview; no inferred ownership or meter topology."""
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_safe

from .access_control import AccessAssignment
from .access_policy import ScopeType
from .access_resolver import can
from .access_scope import ScopeRef
from .models import Account, LandPlot, Meter, Person, PlotRelation, ResidentAccess, SupplyNode, WaterGroup
from .portal_permissions import PortalGrant
from .staff_workspace import _base_context

LIMIT = 100
SEARCH_LIMIT = 30
REQUIRED = ("accounts.view", "plots.view", "relations.view", "water.meters.view",
            "registry.view", "registry.contacts.view", "access.assignment.manage")


def can_use_workbench(user):
    return bool(user.is_active and user.is_staff and all(
        can(user, code, scope=ScopeRef(ScopeType.ALL)) for code in REQUIRED
    ))


def _active(qs, today):
    return qs.filter(starts__lte=today).filter(Q(ends__isnull=True) | Q(ends__gt=today))


def _bounded(qs, limit=LIMIT):
    rows = list(qs[:limit + 1])
    return rows[:limit], len(rows) > limit


def _url(kind, pk):
    return f'{reverse("staff_workspace:workbench")}?kind={kind}&id={pk}'


def _scope_labels(assignments):
    models = {"account": Account, "land_plot": LandPlot, "person": Person,
              "water_group": WaterGroup, "supply_node": SupplyNode}
    names = {}
    for kind, model in models.items():
        ids = {a.scope_object_id for a in assignments if a.scope_type == kind}
        names[kind] = model.objects.in_bulk(ids) if ids else {}
    for a in assignments:
        obj = names.get(a.scope_type, {}).get(a.scope_object_id)
        a.scope_label = "Весь ТСН" if a.scope_type == "all" else str(obj) if obj else f'{a.scope_type} · ID {a.scope_object_id}'


@require_safe
def workbench(request):
    if not can_use_workbench(request.user):
        raise PermissionDenied
    context = _base_context(request, section="more")
    q = " ".join(request.GET.get("q", "").split())[:160]
    kind, raw_id = request.GET.get("kind", ""), request.GET.get("id", "")
    models = {"account": Account, "plot": LandPlot, "person": Person}
    if (kind or raw_id) and (kind not in models or not raw_id.isascii() or not raw_id.isdecimal() or len(raw_id) > 12):
        return HttpResponseBadRequest("Некорректная карточка")
    results, search_truncated = [], False
    if q:
        predicates = {
            "account": Q(plot__icontains=q) | Q(number__icontains=q),
            "plot": Q(label__icontains=q) | Q(address__icontains=q) | Q(cadastral_number__icontains=q),
            "person": (
                Q(full_name__icontains=q)
                | Q(phone__icontains=q)
                | Q(email__icontains=q)
                | Q(resident_identity__user__username__icontains=q)
            ),
        }
        labels = {"account": "Лицевой счёт", "plot": "Участок", "person": "Житель"}
        for result_kind, model in models.items():
            predicate = predicates[result_kind]
            if q.isascii() and q.isdecimal() and len(q) <= 12:
                predicate |= Q(pk=int(q))
            rows, cut = _bounded(model.objects.filter(predicate, archived=False).order_by("pk"), SEARCH_LIMIT)
            search_truncated |= cut
            results.extend({"label": str(row), "type": labels[result_kind], "url": _url(result_kind, row.pk)} for row in rows)
    context.update(q=q, results=results, search_truncated=search_truncated)
    if kind:
        selected = get_object_or_404(models[kind], pk=int(raw_id))
        today = timezone.localdate()
        relations = _active(PlotRelation.objects.select_related("person", "plot", "plot__account"), today)
        grants = _active(PortalGrant.objects.select_related("person", "account"), today)
        legacy = _active(ResidentAccess.objects.select_related("user", "account", "user__resident_identity__person"), today)
        if kind == "person":
            relations = relations.filter(person=selected)
            grants = grants.filter(person=selected)
            legacy = legacy.filter(user__resident_identity__person=selected)
            accounts = Account.objects.filter(
                Q(pk__in=relations.values("plot__account_id")) |
                Q(pk__in=grants.values("account_id")) | Q(pk__in=legacy.values("account_id"))
            )
        else:
            account_id = selected.pk if kind == "account" else selected.account_id
            relations = relations.filter(plot__account_id=account_id) if kind == "account" else relations.filter(plot=selected)
            grants = grants.filter(account_id=account_id) if account_id else grants.none()
            legacy = legacy.filter(account_id=account_id) if account_id else legacy.none()
            accounts = Account.objects.filter(pk=account_id) if account_id else Account.objects.none()
        # SQL subqueries preserve all genuine links even if a displayed section is capped.
        people = Person.objects.filter(
            Q(pk=selected.pk if kind == "person" else None) |
            Q(pk__in=relations.values("person_id")) | Q(pk__in=grants.values("person_id")) |
            Q(resident_identity__user_id__in=legacy.values("user_id"))
        )
        assignments = _active(AccessAssignment.objects.filter(person__in=people, revoked_at__isnull=True).select_related("person"), today)
        meters = Meter.objects.filter(account__in=accounts).select_related("account", "node", "group").order_by("pk")
        plots = LandPlot.objects.filter(account__in=accounts).order_by("pk")
        detail_truncated = False
        for name, qs in (("relations", relations), ("grants", grants), ("legacy", legacy),
                         ("assignments", assignments), ("meters", meters), ("plots", plots), ("accounts", accounts)):
            context[name], cut = _bounded(qs.order_by("pk"))
            detail_truncated |= cut
        _scope_labels(context["assignments"])
        fields = ("can_view_account", "can_view_finance", "can_submit_water", "can_view_documents", "can_use_appeals", "can_represent")
        for grant in context["grants"]:
            grant.rights_label = "; ".join(str(PortalGrant._meta.get_field(f).verbose_name) for f in fields if getattr(grant, f)) or "Активные функции не указаны"
        edit_url = None
        edit_label = None
        if kind == "person" and can(request.user, "registry.edit", scope=ScopeRef(ScopeType.PERSON, selected.pk)):
            edit_url = reverse("staff_workspace:person_edit", args=[selected.pk])
            edit_label = "Редактировать данные"
        elif kind == "plot" and can(request.user, "plots.edit", scope=ScopeRef(ScopeType.LAND_PLOT, selected.pk)):
            edit_url = reverse("staff_workspace:plot_edit", args=[selected.pk])
            edit_label = "Изменить адрес"
        elif kind == "account" and can(request.user, "accounts.edit", scope=ScopeRef(ScopeType.ACCOUNT, selected.pk)):
            edit_url = reverse("staff_workspace:account_edit", args=[selected.pk])
            edit_label = "Редактировать карточку"
        access_url = None
        meter_bind_url = None
        if kind == "person" and can(
            request.user, "access.view", scope=ScopeRef(ScopeType.ALL)
        ):
            access_url = reverse("staff_workspace:access_person", args=[selected.pk])
        if kind == "account" and any(
            can(request.user, "water.topology.manage", scope=ScopeRef(ScopeType.SUPPLY_NODE, node.pk))
            for node in SupplyNode.objects.all()
        ):
            meter_bind_url = reverse("staff_workspace:meter_bind", args=[selected.pk])
        context.update(
            selected=selected, kind=kind, detail_truncated=detail_truncated, today=today,
            edit_url=edit_url, edit_label=edit_label, access_url=access_url,
            meter_bind_url=meter_bind_url,
        )
    response = TemplateResponse(request, "water/work/workbench.html", context)
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


workspace_workbench = admin.site.admin_view(workbench)
