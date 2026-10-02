[Reading 29 lines from start (total: 29 lines, 0 remaining)]

from django.db.models import Count, Q

from .security_models import SecurityAlert


def security_alerts(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_staff:
        return {
            "can_view_security_alerts": False,
            "security_alert_count": 0,
            "security_critical_count": 0,
        }
    can_view = user.is_superuser or user.has_perm("water.view_securityalert")
    if not can_view:
        return {
            "can_view_security_alerts": False,
            "security_alert_count": 0,
            "security_critical_count": 0,
        }
    counts = SecurityAlert.objects.filter(resolved_at__isnull=True).aggregate(
        total=Count("id"),
        critical=Count("id", filter=Q(severity=SecurityAlert.SEVERITY_CRITICAL)),
    )
    return {
        "can_view_security_alerts": True,
        "security_alert_count": counts["total"] or 0,
        "security_critical_count": counts["critical"] or 0,
    }

[executed on device: sandbox (2ce8fd8f-c8b1-4737-95b3-20fa4189189e)]