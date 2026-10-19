"""Read-only model metadata; never inspect managers, rows or field defaults."""
from django.apps import apps
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from django.views.decorators.http import require_GET

from .staff_workspace import _base_context, _capabilities


SECTIONS = [
    ("residents", "Жители и участки", "#287858"),
    ("water", "Вода", "#237ab0"),
    ("finance", "Финансы", "#966419"),
    ("access", "Доступы и роли", "#7855a6"),
    ("board", "Правление", "#ab4d73"),
    ("documents", "Документы и новости", "#b45827"),
    ("forum", "Форум", "#287c83"),
    ("service", "Служебное", "#707780"),
    ("other", "Прочее", "#656b30"),
]


def can_view_db_map(user, capabilities=None):
    # Same gate as the administrative service links in work/more.html.
    capabilities = capabilities if capabilities is not None else _capabilities(user)
    return user.is_superuser or capabilities["can_edit_accounts"] or capabilities["can_manage_finance_policy"]


def _section(model):
    meta = model._meta
    name = meta.object_name.lower()
    if name.startswith("historical") or meta.app_label not in {"water", "forum", "public_site", "auth"}:
        return "service"
    if meta.app_label == "auth" or name == "user":
        return "access"
    if meta.app_label == "forum":
        return "forum"
    if meta.app_label == "public_site" or "document" in name:
        return "documents"
    if any(part in name for part in ("poll", "ballot", "board", "governance")):
        return "board"
    if any(part in name for part in ("access", "role", "grant", "invite", "identity", "password", "delegation", "security")):
        return "access"
    if any(part in name for part in ("billing", "charge", "payment", "tariff", "import")):
        return "finance"
    if any(part in name for part in ("water", "meter", "reading", "supply", "consumption")) or name == "membership":
        return "water"
    if name in {"person", "landplot", "plotrelation", "account", "tsnmembership"}:
        return "residents"
    return "other"


def build_schema():
    models = sorted(apps.get_models(), key=lambda model: model._meta.label_lower)
    nodes, relations = [], []
    labels = {model._meta.label for model in models}
    for model in models:
        meta = model._meta
        fields = []
        for field in (*meta.fields, *meta.many_to_many):
            fields.append({
                "name": field.name, "verbose_name": str(field.verbose_name),
                "type": field.get_internal_type(),
                "required": not field.blank and not field.null and not field.auto_created,
                "nullable": field.null, "primary_key": field.primary_key,
            })
            if field.is_relation and field.related_model._meta.label in labels:
                target = field.related_model._meta
                kind = "m2m" if field.many_to_many else "o2o" if field.one_to_one else "fk"
                caption = {"m2m": "многие ко многим", "o2o": "один к одному"}.get(kind)
                relations.append({
                    "source": meta.label, "target": target.label, "field": field.name,
                    "kind": kind,
                    "label": caption or f"у «{target.verbose_name}» много «{meta.verbose_name_plural}»",
                })
        nodes.append({"id": meta.label, "model": meta.object_name,
                      "verbose_name": str(meta.verbose_name), "section": _section(model),
                      "fields": fields})
    return {"sections": [{"id": key, "label": label, "color": color} for key, label, color in SECTIONS],
            "nodes": nodes, "relations": relations}


@require_GET
def db_map(request):
    if not can_view_db_map(request.user):
        raise PermissionDenied
    context = _base_context(request, section="more")
    context["db_schema"] = build_schema()
    return TemplateResponse(request, "water/work/db_map.html", context)


workspace_db_map = admin.site.admin_view(db_map)
