from django.contrib import admin

from .staff_workspace import dashboard


def tasks(request):
    """Render the existing permission-aware attention queue as a dedicated tab."""
    response = dashboard(request)
    response.template_name = "water/work/tasks.html"
    response.context_data["workspace_section"] = "work"
    return response


workspace_tasks = admin.site.admin_view(tasks)
