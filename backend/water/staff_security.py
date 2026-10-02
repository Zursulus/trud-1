
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse

from .security_models import SecurityAlert
from .staff_workspace import _base_context


def _can_view(user):
    return user.is_superuser or user.has_perm("water.view_securityalert")


def _can_change(user):
    return user.is_superuser or user.has_perm("water.change_securityalert")


def security_alerts(request):
    if not _can_view(request.user):
        raise PermissionDenied

    state = request.GET.get("state") or "open"
    if state not in {"open", "resolved", "all"}:
        state = "open"

    if request.method == "POST":
        if not _can_change(request.user):
            raise PermissionDenied
        alert_id = request.POST.get("alert_id")
        alert = get_object_or_404(SecurityAlert, pk=alert_id)
        alert.resolve(request.user)
        messages.success(request, "Сигнал безопасности отмечен как проверенный.")
        return HttpResponseRedirect(reverse("staff_workspace:security") + f"?state={state}")

    queryset = SecurityAlert.objects.select_related("account", "appeal")
    if state == "open":
        queryset = queryset.filter(resolved_at__isnull=True)
    elif state == "resolved":
        queryset = queryset.filter(resolved_at__isnull=False)

    page = Paginator(queryset.order_by("resolved_at", "-created_at", "-id"), 40).get_page(
        request.GET.get("page")
    )
    context = _base_context(request, section="security")
    context.update({
        "security_state": state,
        "page": page,
        "can_resolve_security_alerts": _can_change(request.user),
        "open_alert_count": SecurityAlert.objects.filter(resolved_at__isnull=True).count(),
    })
    return TemplateResponse(request, "water/work/security.html", context)


workspace_security = admin.site.admin_view(security_alerts)
