from urllib.parse import urlencode

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse

from .access_policy import ScopeType
from .access_resolver import can
from .access_scope import ScopeRef
from .models import Account, LandPlot, Person
from .staff_workspace import _base_context, scoped_accounts


STALE_MESSAGE = "Запись уже изменена другим пользователем. Обновите страницу и повторите правку."


class VersionedModelForm(forms.ModelForm):
    version = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.fields["version"].initial = self.instance.version

    def clean_version(self):
        submitted = self.cleaned_data["version"]
        if self.instance and self.instance.pk:
            current = type(self.instance).objects.only("version").get(pk=self.instance.pk).version
            if submitted != current:
                raise forms.ValidationError(STALE_MESSAGE)
        return submitted


class PersonEditForm(VersionedModelForm):
    class Meta:
        model = Person
        fields = ("full_name", "phone", "email", "notes", "version")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class LandPlotEditForm(VersionedModelForm):
    class Meta:
        model = LandPlot
        fields = ("label", "address", "cadastral_number", "area_m2", "notes", "version")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}


class AccountContactEditForm(VersionedModelForm):
    class Meta:
        model = Account
        fields = ("plot", "contact_name", "phone", "notes", "version")
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, include_contacts=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not include_contacts:
            # Excluded ModelForm fields retain their stored values, including on
            # forged POSTs. Legacy contacts have no verified Person association.
            self.fields.pop("contact_name")
            self.fields.pop("phone")


def _save_versioned(form, *, actor, reason):
    obj = form.save(commit=False)
    obj._history_user = actor
    obj._change_reason = reason
    try:
        obj.save()
    except ValidationError as error:
        if hasattr(error, "message_dict"):
            for field, errors in error.message_dict.items():
                target = field if field in form.fields else None
                for message in errors:
                    form.add_error(target, message)
        else:
            for message in error.messages:
                form.add_error(None, message)
        return None
    return obj


def _render(request, *, form, title, eyebrow, cancel_url, section, explanation):
    context = _base_context(request, section=section)
    context.update({
        "form": form,
        "title": title,
        "eyebrow": eyebrow,
        "cancel_url": cancel_url,
        "explanation": explanation,
    })
    return TemplateResponse(request, "water/work/registry/edit.html", context)


def person_edit(request, person_id):
    person = get_object_or_404(Person, pk=person_id)
    scope = ScopeRef(ScopeType.PERSON, person.pk)
    if not request.user.is_staff or not can(request.user, "registry.edit", scope=scope):
        raise PermissionDenied
    if not can(request.user, "registry.contacts.view", scope=scope):
        raise PermissionDenied

    form = PersonEditForm(request.POST or None, instance=person)
    cancel_url = reverse("staff_workspace:access_person", args=[person.pk])
    if request.method == "POST" and form.is_valid():
        if _save_versioned(form, actor=request.user, reason="Контактные данные изменены в Staff Workspace"):
            messages.success(request, "Данные человека сохранены. История изменения зафиксирована.")
            return HttpResponseRedirect(cancel_url)
    return _render(
        request, form=form, title=f"Редактировать: {person.full_name}",
        eyebrow="Данные жителя", cancel_url=cancel_url, section="access",
        explanation="Исправьте ФИО, телефон или электронную почту. Связи с участками и доступы сохраняются.",
    )


def account_edit(request, account_id):
    account = get_object_or_404(scoped_accounts(request.user), pk=account_id)
    scope = ScopeRef(ScopeType.ACCOUNT, account.pk)
    if not request.user.is_staff or not can(request.user, "accounts.edit", scope=scope):
        raise PermissionDenied

    include_contacts = can(
        request.user, "registry.contacts.view", scope=ScopeRef(ScopeType.ALL),
    )
    form = AccountContactEditForm(request.POST or None, instance=account, include_contacts=include_contacts)
    cancel_url = reverse("staff_workspace:account", args=[account.pk])
    q = " ".join((request.GET.get("q") or "").split())[:160]
    if q:
        cancel_url += "?" + urlencode({"q": q})
    if request.method == "POST" and form.is_valid():
        if _save_versioned(form, actor=request.user, reason="Контактная карточка лицевого счёта изменена в Staff Workspace"):
            messages.success(request, "Карточка лицевого счёта сохранена.")
            return HttpResponseRedirect(cancel_url)
    return _render(
        request, form=form, title=f"Карточка лицевого счёта {account.number or account.pk}",
        eyebrow="Лицевой счёт", cancel_url=cancel_url, section="accounts",
        explanation=("Исправьте обозначение участка, контакт или заметку. Доступы и связи сохраняются."
                     if include_contacts else "Исправьте обозначение участка или рабочую заметку. Доступы и связи сохраняются."),
    )


def land_plot_edit(request, plot_id):
    plot = get_object_or_404(LandPlot.objects.select_related("account"), pk=plot_id)
    scope = ScopeRef(ScopeType.LAND_PLOT, plot.pk)
    if not request.user.is_staff or not can(request.user, "plots.edit", scope=scope):
        raise PermissionDenied

    form = LandPlotEditForm(request.POST or None, instance=plot)
    if plot.account_id and can(request.user, "accounts.view", scope=ScopeRef(ScopeType.ACCOUNT, plot.account_id)):
        cancel_url = reverse("staff_workspace:account", args=[plot.account_id])
    else:
        cancel_url = reverse("staff_workspace:accounts")

    if request.method == "POST" and form.is_valid():
        if _save_versioned(form, actor=request.user, reason="Данные участка изменены в Staff Workspace"):
            messages.success(request, "Адрес и данные участка сохранены. История изменения зафиксирована.")
            return HttpResponseRedirect(cancel_url)
    return _render(
        request, form=form, title=f"Редактировать участок: {plot.label}",
        eyebrow="Участок", cancel_url=cancel_url, section="accounts",
        explanation="Исправьте адрес и реквизиты участка. Владение, лицевой счёт и доступы сохраняются.",
    )


workspace_person_edit = admin.site.admin_view(person_edit)
workspace_account_edit = admin.site.admin_view(account_edit)
workspace_land_plot_edit = admin.site.admin_view(land_plot_edit)
