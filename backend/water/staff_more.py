from pathlib import Path

from django.contrib import admin
from django.http import FileResponse, Http404
from django.template.response import TemplateResponse

from .staff_workspace import _base_context


ROLES_GUIDE_DIR = Path("/var/lib/trud-1/internal-docs")
ROLES_GUIDE_FILE = ROLES_GUIDE_DIR / "roles-guide-current.pdf"
ROLES_GUIDE_VERSION_FILE = ROLES_GUIDE_DIR / "roles-guide-version.txt"


def _roles_guide_version():
    try:
        value = ROLES_GUIDE_VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return value[:40]


def more(request):
    context = _base_context(request, section="more")
    context["roles_guide_available"] = ROLES_GUIDE_FILE.is_file()
    context["roles_guide_version"] = _roles_guide_version()
    return TemplateResponse(request, "water/work/more.html", context)


def roles_guide(request):
    if not ROLES_GUIDE_FILE.is_file():
        raise Http404("Инструкция по ролям пока не загружена")
    response = FileResponse(
        ROLES_GUIDE_FILE.open("rb"),
        content_type="application/pdf",
        filename="trud1_roles_guide.pdf",
    )
    response["Cache-Control"] = "private, no-store"
    return response


workspace_more = admin.site.admin_view(more)
workspace_roles_guide = admin.site.admin_view(roles_guide)
