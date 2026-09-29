from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse

from . import controller_workspace, staff_workspace


def _friendly_redirect(response):
    """Keep legacy backend redirects inside the staff workspace route."""
    if response.status_code not in (301, 302, 303, 307, 308):
        return response
    location = response.get("Location", "")
    legacy = reverse("water_controller_workspace")
    if not location.startswith(legacy):
        return response
    suffix = location[len(legacy):]
    return HttpResponseRedirect(reverse("staff_workspace:water_line") + suffix)


def line_workspace(request):
    """Reuse the proven line workflow, but render it in the simple staff shell."""
    response = controller_workspace.controller_workspace(request)
    response = _friendly_redirect(response)
    if not hasattr(response, "context_data"):
        return response

    response.template_name = "water/work/line_water.html"
    response.context_data.update(staff_workspace._base_context(request, section="water"))
    response.context_data["workspace_section"] = "water"
    return response


def water_entry(request):
    """Skip the intermediate water dashboard for a line-only worker."""
    response = staff_workspace.water_dashboard(request)
    context = getattr(response, "context_data", {})
    if context.get("can_use_controller_workspace") and not context.get("can_moderate_submissions"):
        return HttpResponseRedirect(reverse("staff_workspace:water_line"))
    return response


workspace_line = admin.site.admin_view(line_workspace)
workspace_water_entry = admin.site.admin_view(water_entry)
