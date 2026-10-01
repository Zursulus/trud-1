from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from .access_resolver import can_any
from .access_scope import can_on_record, scoped_records
from .billing import account_totals
from .finance_workflow import (
    allocate_confirmed_payment,
    approve_billing_period,
    approve_charge,
    calculate_billing_period,
    cancel_charge,
    close_billing_period,
    confirm_payment,
    create_payment,
    payment_account_queryset,
    reverse_payment,
)
from .models import Account, BillingPeriod, Charge, Payment, PaymentAllocation
from .staff_workspace import _base_context


MONEY_FIELD = DecimalField(max_digits=14, decimal_places=2)


class PaymentCreateForm(forms.Form):
    account = forms.ModelChoiceField(label="Лицевой счёт", queryset=Account.objects.none())
    paid_on = forms.DateField(label="Дата оплаты", widget=forms.DateInput(attrs={"type": "date"}))
    amount = forms.DecimalField(label="Сумма, ₽", max_digits=14, decimal_places=2, min_value=Decimal("0.01"))
    method = forms.ChoiceField(label="Способ", choices=Payment._meta.get_field("method").choices)
    reference = forms.CharField(label="Номер / назначение платежа", max_length=300, required=False)
    notes = forms.CharField(label="Примечание", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = payment_account_queryset(actor=actor)
        if not self.is_bound:
            self.fields["paid_on"].initial = timezone.localdate()


def _require_finance_view(request):
    if not request.user.is_staff or not can_any(request.user, "finance.view"):
        raise PermissionDenied


def _can(user, capability, *, account_id=None):
    if account_id is not None:
        return can_on_record(user, capability, account_id=account_id)
    return user.is_superuser or can_any(user, capability)


def _finance_periods(user):
    return scoped_records(BillingPeriod.objects.all(), user, "finance.view", account_field="charge__account_id").distinct()


def _payments_with_remaining(queryset):
    allocated = Coalesce(
        Sum("allocations__amount"),
        Value(Decimal("0.00")),
        output_field=MONEY_FIELD,
    )
    return queryset.annotate(
        allocated_total=allocated,
    ).annotate(
        remaining=ExpressionWrapper(F("amount") - F("allocated_total"), output_field=MONEY_FIELD),
    )


def finance_dashboard(request):
    _require_finance_view(request)
    context = _base_context(request, section="finance")
    draft_charges = scoped_records(Charge.objects.filter(status="draft"), request.user, "finance.view")
    payments = scoped_records(Payment.objects.all(), request.user, "finance.view")
    pending_payments = payments.filter(status="pending")
    unallocated = _payments_with_remaining(payments.filter(status="confirmed")).filter(remaining__gt=0)
    context.update({
        "draft_charge_count": draft_charges.count(),
        "draft_charge_amount": draft_charges.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"),
        "pending_payment_count": pending_payments.count(),
        "pending_payment_amount": pending_payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"),
        "unallocated_payment_count": unallocated.count(),
        "periods": list(_finance_periods(request.user).order_by("-starts", "-id")[:8]),
        "recent_payments": list(
            _payments_with_remaining(payments.select_related("account"))
            .order_by("-paid_on", "-id")[:8]
        ),
        "can_add_payment": _can(request.user, "finance.payment.create"),
    })
    return TemplateResponse(request, "water/work/finance/dashboard.html", context)


def period_detail(request, period_id):
    _require_finance_view(request)
    period = get_object_or_404(_finance_periods(request.user), pk=period_id)

    if request.method == "POST":
        action = request.POST.get("action") or ""
        try:
            if action == "calculate":
                results = calculate_billing_period(period_id=period.pk, actor=request.user)
                counts = {key: 0 for key in ("created", "updated", "review", "skipped")}
                for result in results:
                    counts[result.outcome if result.outcome in counts else "skipped"] += 1
                messages.success(
                    request,
                    "Расчёт завершён: создано {created}, обновлено {updated}; проверить {review}, пропущено {skipped}.".format(**counts),
                )
            elif action in {"approve_charge", "cancel_charge"}:
                charge_id = request.POST.get("charge_id")
                charge = get_object_or_404(scoped_records(Charge.objects.all(), request.user, "finance.view"), pk=charge_id, period=period)
                if action == "approve_charge":
                    approve_charge(charge_id=charge.pk, actor=request.user)
                    messages.success(request, f"Начисление №{charge.pk} утверждено.")
                else:
                    cancel_charge(charge_id=charge.pk, actor=request.user)
                    messages.success(request, f"Черновик №{charge.pk} отменён.")
            elif action == "approve_period":
                approve_billing_period(period_id=period.pk, actor=request.user)
                messages.success(request, "Расчётный период утверждён.")
            elif action == "close_period":
                close_billing_period(period_id=period.pk, actor=request.user)
                messages.success(request, "Расчётный период закрыт.")
            else:
                messages.error(request, "Неизвестное финансовое действие.")
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
        return HttpResponseRedirect(reverse("staff_workspace:finance_period", args=[period.pk]))

    period.refresh_from_db()
    charges = list(
        scoped_records(Charge.objects.filter(period=period), request.user, "finance.view")
        .select_related("account")
        .order_by("status", "account__plot", "account__number", "id")
    )
    for charge in charges:
        charge.can_approve = _can(request.user, "finance.charge.approve", account_id=charge.account_id)
        charge.can_cancel = _can(request.user, "finance.charge.cancel", account_id=charge.account_id)
    draft_count = sum(1 for charge in charges if charge.status == "draft")
    context = _base_context(request, section="finance")
    context.update({
        "period": period,
        "charges": charges,
        "draft_count": draft_count,
        "approved_amount": sum((charge.amount for charge in charges if charge.status == "approved"), Decimal("0.00")),
        "draft_amount": sum((charge.amount for charge in charges if charge.status == "draft"), Decimal("0.00")),
        "can_calculate": _can(request.user, "finance.period.calculate") and period.status not in ("approved", "closed"),
        "can_change_charge": any(charge.can_approve or charge.can_cancel for charge in charges),
        "can_approve_period": _can(request.user, "finance.period.approve") and period.status == "calculated" and not Charge.objects.filter(period=period, status="draft").exists(),
        "can_close_period": _can(request.user, "finance.period.close") and period.status == "approved",
    })
    return TemplateResponse(request, "water/work/finance/period.html", context)


def payment_list(request):
    _require_finance_view(request)
    state = request.GET.get("state") or "attention"
    if state not in {"attention", "pending", "unallocated", "confirmed", "reversed", "all"}:
        state = "attention"
    q = " ".join((request.GET.get("q") or "").split())[:160]
    payments = _payments_with_remaining(scoped_records(Payment.objects.select_related("account"), request.user, "finance.view"))
    if state == "attention":
        payments = payments.filter(Q(status="pending") | Q(status="confirmed", remaining__gt=0))
    elif state == "pending":
        payments = payments.filter(status="pending")
    elif state == "unallocated":
        payments = payments.filter(status="confirmed", remaining__gt=0)
    elif state == "confirmed":
        payments = payments.filter(status="confirmed")
    elif state == "reversed":
        payments = payments.filter(status="reversed")
    if q:
        payments = payments.filter(
            Q(account__number__icontains=q)
            | Q(account__plot__icontains=q)
            | Q(reference__icontains=q)
        )
    page = Paginator(payments.order_by("-paid_on", "-id"), 30).get_page(request.GET.get("page"))
    context = _base_context(request, section="finance")
    context.update({
        "payment_state": state,
        "q": q,
        "page": page,
        "can_add_payment": _can(request.user, "finance.payment.create"),
    })
    return TemplateResponse(request, "water/work/finance/payments.html", context)


def payment_create(request):
    _require_finance_view(request)
    if not _can(request.user, "finance.payment.create"):
        raise PermissionDenied
    form = PaymentCreateForm(request.POST or None, actor=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            payment = create_payment(actor=request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, "; ".join(error.messages))
        else:
            messages.success(request, "Оплата внесена и ожидает проверки.")
            return HttpResponseRedirect(reverse("staff_workspace:finance_payment", args=[payment.pk]))
    context = _base_context(request, section="finance")
    context["form"] = form
    return TemplateResponse(request, "water/work/finance/payment_form.html", context)


def payment_detail(request, payment_id):
    _require_finance_view(request)
    payment = get_object_or_404(scoped_records(Payment.objects.select_related("account"), request.user, "finance.view"), pk=payment_id)
    if request.method == "POST":
        action = request.POST.get("action") or ""
        try:
            if action == "confirm":
                confirm_payment(payment_id=payment.pk, actor=request.user)
                messages.success(request, "Оплата подтверждена.")
            elif action == "allocate":
                allocations, note = allocate_confirmed_payment(payment_id=payment.pk, actor=request.user)
                if allocations:
                    messages.success(request, note)
                else:
                    messages.warning(request, note)
            elif action == "reverse":
                reverse_payment(payment_id=payment.pk, actor=request.user)
                messages.success(request, "Оплата отменена. Её распределения сохранены в истории и больше не уменьшают долг.")
            else:
                messages.error(request, "Неизвестное финансовое действие.")
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
        return HttpResponseRedirect(reverse("staff_workspace:finance_payment", args=[payment.pk]))

    payment.refresh_from_db()
    allocations = list(
        PaymentAllocation.objects.filter(payment=payment)
        .select_related("charge", "charge__period")
        .order_by("charge__period__starts", "charge_id")
    )
    allocated_total = sum((item.amount for item in allocations), Decimal("0.00"))
    remaining = payment.amount - allocated_total if payment.status == "confirmed" else Decimal("0.00")
    context = _base_context(request, section="finance")
    context.update({
        "payment": payment,
        "allocations": allocations,
        "allocated_total": allocated_total,
        "remaining": remaining,
        "can_confirm": _can(request.user, "finance.payment.confirm", account_id=payment.account_id) and payment.status == "pending",
        "can_allocate": _can(request.user, "finance.payment.allocate", account_id=payment.account_id) and payment.status == "confirmed" and remaining > 0,
        "can_reverse": _can(request.user, "finance.payment.reverse", account_id=payment.account_id) and payment.status == "confirmed",
    })
    return TemplateResponse(request, "water/work/finance/payment.html", context)


def account_finance(request, account_id):
    _require_finance_view(request)
    account = get_object_or_404(scoped_records(Account.objects.all(), request.user, "finance.view", account_field="pk"), pk=account_id)
    charges = list(
        Charge.objects.filter(account=account)
        .select_related("period")
        .order_by("-period__starts", "-id")[:30]
    )
    payments = list(
        _payments_with_remaining(Payment.objects.filter(account=account))
        .order_by("-paid_on", "-id")[:30]
    )
    context = _base_context(request, section="finance")
    context.update({
        "account": account,
        "totals": account_totals(account),
        "charges": charges,
        "payments": payments,
    })
    return TemplateResponse(request, "water/work/finance/account.html", context)


workspace_finance = admin.site.admin_view(finance_dashboard)
workspace_finance_period = admin.site.admin_view(period_detail)
workspace_finance_payments = admin.site.admin_view(payment_list)
workspace_finance_payment_create = admin.site.admin_view(payment_create)
workspace_finance_payment = admin.site.admin_view(payment_detail)
workspace_finance_account = admin.site.admin_view(account_finance)
