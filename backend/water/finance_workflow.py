from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .access_resolver import can_any
from .access_scope import can_on_record, scoped_records
from .billing import allocate_payment, calculate_period
from .models import Account, BillingPeriod, Charge, Payment


def _require(actor, capability, *, account_id=None):
    allowed = can_any(actor, capability) if account_id is None else can_on_record(actor, capability, account_id=account_id)
    if not actor.is_staff or not allowed:
        raise PermissionDenied


@transaction.atomic
def calculate_billing_period(*, period_id, actor):
    _require(actor, "finance.period.calculate")
    period = BillingPeriod.objects.select_for_update().get(pk=period_id)
    return calculate_period(period, actor=actor)


@transaction.atomic
def approve_charge(*, charge_id, actor):
    _require(actor, "finance.charge.approve")
    charge = Charge.objects.select_for_update(of=("self",)).select_related("period", "account").get(pk=charge_id)
    _require(actor, "finance.charge.approve", account_id=charge.account_id)
    if charge.status != "draft":
        raise ValidationError("Изменить статус можно только у черновика начисления.")
    charge.status = "approved"
    charge._history_user = actor
    charge._change_reason = "Утверждение начисления в рабочей базе"
    charge.save()
    return charge


@transaction.atomic
def cancel_charge(*, charge_id, actor):
    _require(actor, "finance.charge.cancel")
    charge = Charge.objects.select_for_update(of=("self",)).select_related("period", "account").get(pk=charge_id)
    _require(actor, "finance.charge.cancel", account_id=charge.account_id)
    if charge.status != "draft":
        raise ValidationError("Отменить можно только черновик начисления.")
    charge.status = "cancelled"
    charge._history_user = actor
    charge._change_reason = "Отмена черновика начисления в рабочей базе"
    charge.save()
    return charge


@transaction.atomic
def approve_billing_period(*, period_id, actor):
    _require(actor, "finance.period.approve")
    period = BillingPeriod.objects.select_for_update().get(pk=period_id)
    if period.status != "calculated":
        raise ValidationError("Утвердить можно только рассчитанный период.")
    if Charge.objects.filter(period=period, status="draft").exists():
        raise ValidationError("Сначала утвердите или отмените все черновики начислений периода.")
    if not Charge.objects.filter(period=period, status="approved").exists():
        raise ValidationError("В периоде нет утверждённых начислений.")
    period.status = "approved"
    period._history_user = actor
    period._change_reason = "Расчётный период утверждён в рабочей базе"
    period.save()
    return period


@transaction.atomic
def close_billing_period(*, period_id, actor):
    _require(actor, "finance.period.close")
    period = BillingPeriod.objects.select_for_update().get(pk=period_id)
    if period.status != "approved":
        raise ValidationError("Закрыть можно только утверждённый период.")
    period.status = "closed"
    period._history_user = actor
    period._change_reason = "Расчётный период закрыт в рабочей базе"
    period.save()
    return period


@transaction.atomic
def create_payment(*, actor, account, paid_on, amount, method, reference="", notes=""):
    _require(actor, "finance.payment.create", account_id=account.pk)
    if account.archived:
        raise ValidationError("Нельзя добавить новую оплату в архивный лицевой счёт.")
    payment = Payment(
        account=account,
        paid_on=paid_on,
        amount=amount,
        method=method,
        reference=(reference or "").strip(),
        notes=(notes or "").strip(),
        status="pending",
    )
    payment._history_user = actor
    payment._change_reason = "Оплата внесена в рабочей базе и ожидает проверки"
    payment.save()
    return payment


@transaction.atomic
def confirm_payment(*, payment_id, actor):
    _require(actor, "finance.payment.confirm")
    payment = Payment.objects.select_for_update(of=("self",)).select_related("account").get(pk=payment_id)
    _require(actor, "finance.payment.confirm", account_id=payment.account_id)
    if payment.status != "pending":
        raise ValidationError("Подтвердить можно только оплату, ожидающую проверки.")
    payment.status = "confirmed"
    payment._history_user = actor
    payment._change_reason = "Оплата подтверждена в рабочей базе"
    payment.save()
    return payment


@transaction.atomic
def reverse_payment(*, payment_id, actor):
    _require(actor, "finance.payment.reverse")
    payment = Payment.objects.select_for_update(of=("self",)).select_related("account").get(pk=payment_id)
    _require(actor, "finance.payment.reverse", account_id=payment.account_id)
    if payment.status != "confirmed":
        raise ValidationError("Отменить можно только подтверждённую оплату.")
    payment.status = "reversed"
    payment._history_user = actor
    payment._change_reason = "Подтверждённая оплата отменена в рабочей базе"
    payment.save()
    return payment


@transaction.atomic
def allocate_confirmed_payment(*, payment_id, actor):
    _require(actor, "finance.payment.allocate")
    payment = Payment.objects.select_for_update(of=("self",)).select_related("account").get(pk=payment_id)
    _require(actor, "finance.payment.allocate", account_id=payment.account_id)
    return allocate_payment(payment, actor=actor)


def payment_account_queryset(*, actor):
    accounts = scoped_records(Account.objects.filter(archived=False), actor, "finance.payment.create", account_field="pk")
    return scoped_records(accounts, actor, "finance.view", account_field="pk").order_by("plot", "number", "id")
