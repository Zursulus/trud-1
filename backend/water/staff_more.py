from django.contrib import admin
from django.template.response import TemplateResponse

from .portal_permissions import has_any_portal_access
from .staff_workspace import _base_context


ROLES_GUIDE_VERSION = "1.1"
ROLES_GUIDE_UPDATED = "29.09.2026"


def more(request):
    context = _base_context(request, section="more")
    context["roles_guide_version"] = ROLES_GUIDE_VERSION
    context["has_resident_access"] = has_any_portal_access(request.user)
    return TemplateResponse(request, "water/work/more.html", context)


def roles_guide(request):
    context = _base_context(request, section="more")
    context.update({
        "roles_guide_version": ROLES_GUIDE_VERSION,
        "roles_guide_updated": ROLES_GUIDE_UPDATED,
    })
    return TemplateResponse(request, "water/work/roles_guide.html", context)


workspace_more = admin.site.admin_view(more)
workspace_roles_guide = admin.site.admin_view(roles_guide)
