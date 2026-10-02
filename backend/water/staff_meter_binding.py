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
from .models import Account, Meter, SupplyNode
from .staff_workspace import _base_context


class IndividualMeterBindForm(forms.Form):
    node = forms.ModelChoiceField(label="Общий узел", queryset=SupplyNode.objects.none())
    serial = forms.CharField(label="Номер / обозначение счётчика", max_length=100)
    commissioned_on = forms.DateField(
        label="Установлен / принят на учёт", required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    seal_number = forms.CharField(label="Номер пломбы", max_length=100, required=False)
    notes = forms.CharField(label="Примечание", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["node"].queryset = SupplyNode.objects.filter(
            pk__in=[
                node.pk for node in SupplyNode.objects.all()
                if can(actor, "water.topology.manage", scope=ScopeRef(ScopeType.SUPPLY_NODE, node.pk))
            ]
        ).order_by("name", "pk")


def meter_bind(request, account_id):
    account = get_object_or_404(Account, pk=account_id, archived=False)
    if not request.user.is_staff or not can(
        request.user, "accounts.view", scope=ScopeRef(ScopeType.ACCOUNT, account.pk)
    ):
        raise PermissionDenied

    form = IndividualMeterBindForm(request.POST or None, actor=request.user)
    if request.method == "POST" and form.is_valid():
        node = form.cleaned_data["node"]
        if not can(
            request.user, "water.topology.manage",
            scope=ScopeRef(ScopeType.SUPPLY_NODE, node.pk),
        ):
            raise PermissionDenied
        if Meter.objects.filter(node=node, serial=form.cleaned_data["serial"].strip()).exists():
            form.add_error("serial", "Счётчик с таким номером уже существует на выбранном узле.")
            context = _base_context(request, section="more")
            context.update(account=account, form=form)
            response = TemplateResponse(request, "water/work/meter_bind.html", context)
            response["Cache-Control"] = "private, no-store"
            response["X-Robots-Tag"] = "noindex, nofollow"
            return response
        meter = Meter(
            serial=form.cleaned_data["serial"].strip(),
            kind="individual",
            node=node,
            account=account,
            commissioned_on=form.cleaned_data.get("commissioned_on"),
            seal_number=form.cleaned_data.get("seal_number", "").strip(),
            notes=form.cleaned_data.get("notes", "").strip(),
        )
        meter._history_user = request.user
        meter._change_reason = "Индивидуальный счётчик привязан в Staff Workspace"
        try:
            meter.save()
        except ValidationError as error:
            if hasattr(error, "message_dict"):
                for field, errors in error.message_dict.items():
                    target = field if field in form.fields else None
                    for message in errors:
                        form.add_error(target, message)
            else:
                for message in error.messages:
                    form.add_error(None, message)
        else:
            messages.success(request, f"Счётчик {meter.serial} привязан к {account}. История зафиксирована.")
            return HttpResponseRedirect(
                reverse("staff_workspace:workbench") + f"?kind=account&id={account.pk}"
            )

    context = _base_context(request, section="more")
    context.update(account=account, form=form)
    response = TemplateResponse(request, "water/work/meter_bind.html", context)
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


workspace_meter_bind = admin.site.admin_view(meter_bind)
