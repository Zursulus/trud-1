from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse

from . import controller_workspace, staff_workspace
from .access_resolver import can_any


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
    """Skip the intermediate water dashboard when the user's job is already known."""
    line_senior = (
        can_any(request.user, "water.line_submission.submit")
        or can_any(request.user, "water.observation.review_line")
    )
    if line_senior and not can_any(request.user, "water.observation.finalize"):
        return line_workspace(request)
    return staff_workspace.water_dashboard(request)


workspace_line = admin.site.admin_view(line_workspace)
workspace_water_entry = admin.site.admin_view(water_entry)
