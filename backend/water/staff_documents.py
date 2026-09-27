from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from public_site.models import PublicDocument, PublicDocumentCategory, PublicNews
from public_site.publication_workflow import PUBLICATION_CONFIRMATION_ERROR, apply_publication_state

from .document_workflow import create_account_document, update_account_document
from .models import Account, AccountDocument, DocumentCategory
from .staff_workspace import _base_context


DATETIME_FORMAT = "%Y-%m-%dT%H:%M"


def _can(user, permission):
    return user.is_superuser or user.has_perm(permission)


def _can_use_workspace(user):
    return user.is_superuser or any(
        user.has_perm(permission)
        for permission in (
            "water.view_accountdocument",
            "public_site.view_publicdocument",
            "public_site.view_publicnews",
        )
    )


def _require_workspace(request):
    if not request.user.is_staff or not _can_use_workspace(request.user):
        raise PermissionDenied


def _log_content(actor, obj, action_flag, reason):
    LogEntry.objects.create(
        user=actor,
        content_type=ContentType.objects.get_for_model(obj),
        object_id=str(obj.pk),
        object_repr=str(obj)[:200],
        action_flag=action_flag,
        change_message=f"Рабочая база: {reason}",
    )


class AccountDocumentCreateForm(forms.Form):
    account = forms.ModelChoiceField(label="Лицевой счёт", queryset=Account.objects.none())
    category = forms.ModelChoiceField(label="Вид документа", queryset=DocumentCategory.objects.none())
    title = forms.CharField(label="Название", max_length=200)
    document = forms.FileField(label="Файл")
    published_at = forms.DateTimeField(
        label="Показывать не раньше",
        input_formats=[DATETIME_FORMAT],
        widget=forms.DateTimeInput(format=DATETIME_FORMAT, attrs={"type": "datetime-local"}),
    )
    visible_to_residents = forms.BooleanField(label="Показывать жителям", required=False, initial=True)
    notes = forms.CharField(label="Служебное примечание", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(archived=False).order_by("plot", "number", "id")
        self.fields["category"].queryset = DocumentCategory.objects.filter(active=True).order_by("sort_order", "name")
        if not self.is_bound:
            self.fields["published_at"].initial = timezone.localtime().strftime(DATETIME_FORMAT)


class AccountDocumentEditForm(forms.Form):
    category = forms.ModelChoiceField(label="Вид документа", queryset=DocumentCategory.objects.none())
    title = forms.CharField(label="Название", max_length=200)
    published_at = forms.DateTimeField(
        label="Показывать не раньше",
        input_formats=[DATETIME_FORMAT],
        widget=forms.DateTimeInput(format=DATETIME_FORMAT, attrs={"type": "datetime-local"}),
    )
    visible_to_residents = forms.BooleanField(label="Показывать жителям", required=False)
    notes = forms.CharField(label="Служебное примечание", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    change_reason = forms.CharField(label="Причина изменения", max_length=500, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, item=None, **kwargs):
        self.item = item
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = DocumentCategory.objects.order_by("active", "sort_order", "name")
        if item is not None and not self.is_bound:
            self.initial.update({
                "category": item.category_id,
                "title": item.title,
                "published_at": timezone.localtime(item.published_at).strftime(DATETIME_FORMAT),
                "visible_to_residents": item.visible_to_residents,
                "notes": item.notes,
            })


class PublicationFormMixin:
    confirm_publication = forms.BooleanField(
        label="Проверено: нет персональных, банковских и служебных данных",
        required=False,
    )
    change_reason = forms.CharField(
        label="Причина изменения",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 2}),
    )

    def clean(self):
        data = super().clean()
        if data.get("is_published") and not data.get("confirm_publication"):
            self.add_error("confirm_publication", PUBLICATION_CONFIRMATION_ERROR)
        if self.instance.pk and not (data.get("change_reason") or "").strip():
            self.add_error("change_reason", "При изменении существующей записи укажите причину.")
        return data


class PublicDocumentCreateForm(PublicationFormMixin, forms.ModelForm):
    class Meta:
        model = PublicDocument
        fields = ("category", "title", "description", "document", "document_date", "is_published", "notes")
        widgets = {"document_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"].queryset = PublicDocumentCategory.objects.filter(active=True).order_by("sort_order", "name")


class PublicDocumentEditForm(PublicationFormMixin, forms.ModelForm):
    class Meta:
        model = PublicDocument
        fields = ("category", "title", "description", "document_date", "is_published", "notes")
        widgets = {"document_date": forms.DateInput(attrs={"type": "date"}), "notes": forms.Textarea(attrs={"rows": 3})}


class PublicNewsForm(PublicationFormMixin, forms.ModelForm):
    class Meta:
        model = PublicNews
        fields = ("title", "category", "summary", "body", "published_on", "is_featured", "is_published")
        widgets = {
            "published_on": forms.DateInput(attrs={"type": "date"}),
            "body": forms.Textarea(attrs={"rows": 10}),
            "summary": forms.Textarea(attrs={"rows": 3}),
        }


@transaction.atomic
def _save_public_form(form, *, actor):
    was_existing = bool(form.instance.pk)
    obj = form.save(commit=False)
    apply_publication_state(
        obj,
        actor=actor,
        is_published=form.cleaned_data["is_published"],
        confirmed=form.cleaned_data.get("confirm_publication", False),
    )
    obj.full_clean()
    obj.save()
    reason = (form.cleaned_data.get("change_reason") or "Создание записи").strip()
    _log_content(actor, obj, CHANGE if was_existing else ADDITION, reason)
    return obj


def dashboard(request):
    _require_workspace(request)
    context = _base_context(request, section="documents")
    q = " ".join((request.GET.get("q") or "").split())[:160]
    context["q"] = q

    can_account = _can(request.user, "water.view_accountdocument")
    can_public = _can(request.user, "public_site.view_publicdocument")
    can_news = _can(request.user, "public_site.view_publicnews")
    context.update({
        "can_view_account_documents": can_account,
        "can_add_account_documents": _can(request.user, "water.add_accountdocument"),
        "can_view_public_documents": can_public,
        "can_add_public_documents": _can(request.user, "public_site.add_publicdocument"),
        "can_view_public_news": can_news,
        "can_add_public_news": _can(request.user, "public_site.add_publicnews"),
    })

    if can_account:
        queryset = AccountDocument.objects.select_related("account", "category")
        if q:
            queryset = queryset.filter(
                Q(title__icontains=q)
                | Q(original_name__icontains=q)
                | Q(account__number__icontains=q)
                | Q(account__plot__icontains=q)
                | Q(category__name__icontains=q)
            )
        context.update({
            "account_document_count": AccountDocument.objects.count(),
            "resident_visible_count": AccountDocument.objects.filter(
                visible_to_residents=True, published_at__lte=timezone.now()
            ).count(),
            "account_documents": list(queryset.order_by("-published_at", "-id")[:40]),
        })

    if can_public:
        queryset = PublicDocument.objects.select_related("category", "published_by")
        if q:
            queryset = queryset.filter(
                Q(title__icontains=q) | Q(description__icontains=q) | Q(original_name__icontains=q)
            )
        context.update({
            "public_document_count": PublicDocument.objects.count(),
            "public_document_draft_count": PublicDocument.objects.filter(is_published=False).count(),
            "public_documents": list(queryset.order_by("-document_date", "-id")[:40]),
        })

    if can_news:
        queryset = PublicNews.objects.select_related("published_by")
        if q:
            queryset = queryset.filter(
                Q(title__icontains=q) | Q(summary__icontains=q) | Q(category__icontains=q)
            )
        context.update({
            "news_count": PublicNews.objects.count(),
            "news_draft_count": PublicNews.objects.filter(is_published=False).count(),
            "news_items": list(queryset.order_by("-published_on", "-id")[:30]),
        })

    return TemplateResponse(request, "water/work/documents/dashboard.html", context)


def account_document_create(request):
    if not _can(request.user, "water.add_accountdocument"):
        raise PermissionDenied
    form = AccountDocumentCreateForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            item = create_account_document(actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
        else:
            messages.success(request, "Документ лицевого счёта создан; файл сохранён как неизменяемый оригинал.")
            return HttpResponseRedirect(reverse("staff_workspace:account_document", args=[item.pk]))
    context = _base_context(request, section="documents")
    context.update({"form": form, "mode": "create", "kind": "account"})
    return TemplateResponse(request, "water/work/documents/account_form.html", context)


def account_document_detail(request, document_id):
    if not _can(request.user, "water.view_accountdocument"):
        raise PermissionDenied
    item = get_object_or_404(AccountDocument.objects.select_related("account", "category"), pk=document_id)
    can_change = _can(request.user, "water.change_accountdocument")
    bound_data = request.POST if request.method == "POST" and can_change else None
    form = AccountDocumentEditForm(bound_data, item=item)
    if request.method == "POST":
        if not can_change:
            raise PermissionDenied
        if form.is_valid():
            try:
                update_account_document(item.pk, actor=request.user, **form.cleaned_data)
            except ValidationError as error:
                form.add_error(None, "; ".join(error.messages))
            else:
                messages.success(request, "Метаданные документа обновлены; исходный файл не менялся.")
                return HttpResponseRedirect(reverse("staff_workspace:account_document", args=[item.pk]))
    context = _base_context(request, section="documents")
    context.update({"item": item, "form": form, "mode": "edit", "kind": "account", "can_change": can_change})
    return TemplateResponse(request, "water/work/documents/account_form.html", context)


def account_document_download(request, document_id):
    if not _can(request.user, "water.view_accountdocument"):
        raise PermissionDenied
    item = get_object_or_404(AccountDocument, pk=document_id)
    try:
        stream = item.document.open("rb")
    except (FileNotFoundError, OSError) as error:
        raise Http404 from error
    response = FileResponse(stream, as_attachment=True, filename=item.original_name)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def public_document_create(request):
    if not _can(request.user, "public_site.add_publicdocument"):
        raise PermissionDenied
    form = PublicDocumentCreateForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            item = _save_public_form(form, actor=request.user)
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
        else:
            messages.success(request, "Публичный документ сохранён.")
            return HttpResponseRedirect(reverse("staff_workspace:public_document", args=[item.pk]))
    context = _base_context(request, section="documents")
    context.update({"form": form, "mode": "create"})
    return TemplateResponse(request, "water/work/documents/public_form.html", context)


def public_document_detail(request, document_id):
    if not _can(request.user, "public_site.view_publicdocument"):
        raise PermissionDenied
    item = get_object_or_404(PublicDocument.objects.select_related("category", "published_by"), pk=document_id)
    can_change = _can(request.user, "public_site.change_publicdocument")
    bound_data = request.POST if request.method == "POST" and can_change else None
    form = PublicDocumentEditForm(bound_data, instance=item)
    if request.method == "POST":
        if not can_change:
            raise PermissionDenied
        if form.is_valid():
            try:
                _save_public_form(form, actor=request.user)
            except ValidationError as error:
                form.add_error(None, "; ".join(error.messages))
            else:
                messages.success(request, "Публичный документ обновлён; файл не заменялся.")
                return HttpResponseRedirect(reverse("staff_workspace:public_document", args=[item.pk]))
    context = _base_context(request, section="documents")
    context.update({"item": item, "form": form, "mode": "edit", "can_change": can_change})
    return TemplateResponse(request, "water/work/documents/public_form.html", context)


def public_document_download(request, document_id):
    if not _can(request.user, "public_site.view_publicdocument"):
        raise PermissionDenied
    item = get_object_or_404(PublicDocument, pk=document_id)
    try:
        stream = item.document.open("rb")
    except (FileNotFoundError, OSError) as error:
        raise Http404 from error
    response = FileResponse(stream, as_attachment=True, filename=item.original_name)
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def news_create(request):
    if not _can(request.user, "public_site.add_publicnews"):
        raise PermissionDenied
    form = PublicNewsForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            item = _save_public_form(form, actor=request.user)
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
        else:
            messages.success(request, "Новость сохранена.")
            return HttpResponseRedirect(reverse("staff_workspace:news", args=[item.pk]))
    context = _base_context(request, section="documents")
    context.update({"form": form, "mode": "create"})
    return TemplateResponse(request, "water/work/documents/news_form.html", context)


def news_detail(request, news_id):
    if not _can(request.user, "public_site.view_publicnews"):
        raise PermissionDenied
    item = get_object_or_404(PublicNews.objects.select_related("published_by"), pk=news_id)
    can_change = _can(request.user, "public_site.change_publicnews")
    bound_data = request.POST if request.method == "POST" and can_change else None
    form = PublicNewsForm(bound_data, instance=item)
    if request.method == "POST":
        if not can_change:
            raise PermissionDenied
        if form.is_valid():
            try:
                _save_public_form(form, actor=request.user)
            except ValidationError as error:
                form.add_error(None, "; ".join(error.messages))
            else:
                messages.success(request, "Новость обновлена.")
                return HttpResponseRedirect(reverse("staff_workspace:news", args=[item.pk]))
    context = _base_context(request, section="documents")
    context.update({"item": item, "form": form, "mode": "edit", "can_change": can_change})
    return TemplateResponse(request, "water/work/documents/news_form.html", context)


workspace_documents = admin.site.admin_view(dashboard)
workspace_account_document_create = admin.site.admin_view(account_document_create)
workspace_account_document = admin.site.admin_view(account_document_detail)
workspace_account_document_download = admin.site.admin_view(account_document_download)
workspace_public_document_create = admin.site.admin_view(public_document_create)
workspace_public_document = admin.site.admin_view(public_document_detail)
workspace_public_document_download = admin.site.admin_view(public_document_download)
workspace_news_create = admin.site.admin_view(news_create)
workspace_news = admin.site.admin_view(news_detail)
