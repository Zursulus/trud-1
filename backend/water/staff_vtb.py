from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse

from .access_resolver import can_any
from .models import Account
from .staff_workspace import _base_context
from .vtb_registry import VtbRegistryError, parse_payment_registry


MAX_REGISTRY_BYTES = 5 * 1024 * 1024


class VtbPaymentDryRunForm(forms.Form):
    registry = forms.FileField(label="Реестр принятых платежей ВТБ")

    def clean_registry(self):
        upload = self.cleaned_data["registry"]
        if upload.size > MAX_REGISTRY_BYTES:
            raise forms.ValidationError("Реестр должен быть не больше 5 МБ.")
        return upload


def payment_registry_dryrun(request):
    if not request.user.is_staff or not can_any(request.user, "finance.view"):
        raise PermissionDenied
    form = VtbPaymentDryRunForm(request.POST or None, request.FILES or None)
    report = None
    if request.method == "POST" and form.is_valid():
        try:
            registry = parse_payment_registry(form.cleaned_data["registry"].read())
        except VtbRegistryError as error:
            form.add_error("registry", str(error))
        else:
            numbers = {row.personal_account for row in registry.rows}
            accounts = {
                item.number: item
                for item in Account.objects.filter(number__in=numbers).only(
                    "id", "number", "plot", "archived"
                )
            }
            seen_keys = set()
            rows = []
            for item in registry.rows:
                account = accounts.get(item.personal_account)
                idempotency_key = (
                    item.uni, item.bank_document, item.paid_on.isoformat(),
                    f"{item.operation_amount:.2f}",
                )
                duplicate_in_file = idempotency_key in seen_keys
                seen_keys.add(idempotency_key)
                rows.append({
                    "payment": item,
                    "account": account,
                    "status": (
                        "duplicate"
                        if duplicate_in_file else
                        "matched"
                        if account is not None else
                        "unmatched"
                    ),
                    "idempotency_key": "|".join(idempotency_key),
                })
            report = {
                "registry": registry,
                "rows": rows,
                "matched": sum(row["status"] == "matched" for row in rows),
                "unmatched": sum(row["status"] == "unmatched" for row in rows),
                "duplicates": sum(row["status"] == "duplicate" for row in rows),
            }

    context = _base_context(request, section="finance")
    context.update(form=form, report=report)
    response = TemplateResponse(request, "water/work/finance/vtb_payments_dryrun.html", context)
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


workspace_vtb_payment_dryrun = admin.site.admin_view(payment_registry_dryrun)
