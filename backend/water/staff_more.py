from django.contrib import admin
from django.template.response import TemplateResponse

from .access_policy import CAPABILITIES, ROLE_TEMPLATES, ScopeType
from .portal_permissions import has_any_portal_access
from .staff_workspace import _base_context
from .staff_workbench import can_use_workbench


ROLES_GUIDE_VERSION = "1.2"
ROLES_GUIDE_UPDATED = "19.10.2026"


def more(request):
    context = _base_context(request, section="more")
    context["roles_guide_version"] = ROLES_GUIDE_VERSION
    context["can_use_workbench"] = can_use_workbench(request.user)
    context["has_resident_access"] = has_any_portal_access(request.user)
    return TemplateResponse(request, "water/work/more.html", context)


def roles_guide(request):
    context = _base_context(request, section="more")
    scope_labels = {
        ScopeType.SELF: "свои данные",
        ScopeType.ACCOUNT: "конкретный лицевой счёт",
        ScopeType.LAND_PLOT: "конкретный участок",
        ScopeType.PERSON: "конкретный человек",
        ScopeType.WATER_GROUP: "конкретная линия",
        ScopeType.SUPPLY_NODE: "конкретный общий узел",
        ScopeType.ALL: "весь разрешённый контур",
    }
    service_roles = [
        {
            "code": role.code,
            "label": role.label,
            "scopes": [scope_labels[scope] for scope in role.scopes],
            "functions": [CAPABILITIES[code].label for code in role.capabilities],
        }
        for code, role in ROLE_TEMPLATES.items() if code != "resident_account"
    ]
    context.update({
        "service_roles": service_roles,
        "roles_guide_version": ROLES_GUIDE_VERSION,
        "roles_guide_updated": ROLES_GUIDE_UPDATED,
    })
    return TemplateResponse(request, "water/work/roles_guide.html", context)


workspace_more = admin.site.admin_view(more)
workspace_roles_guide = admin.site.admin_view(roles_guide)

