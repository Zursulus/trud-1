from django.contrib import admin
from django.template.response import TemplateResponse

from .staff_workspace import _base_context


def more(request):
    return TemplateResponse(request, "water/work/more.html", _base_context(request, section="more"))


workspace_more = admin.site.admin_view(more)
