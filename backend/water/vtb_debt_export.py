from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Prefetch

from .models import Charge, PaymentAllocation


@dataclass(frozen=True)
class VtbDebtExportRow:
    account_number: str
    payer_name: str
    address: str
    period: str
    amount: Decimal


@dataclass(frozen=True)
class VtbDebtSkip:
    account_id: int
    period: str
    reason: str


def _validate_text(value, *, maximum, label):
    value = (value or "").strip()
    if not value:
        return None, f"{label}: не заполнено"
    if len(value) > maximum:
        return None, f"{label}: больше {maximum} символов"
    return value, None


def build_vtb_debt_export():
    confirmed_allocations = PaymentAllocation.objects.filter(
        payment__status="confirmed",
    ).select_related("payment")
    charges = (
        Charge.objects.filter(status="approved")
        .select_related("account", "period")
        .prefetch_related(Prefetch("allocations", queryset=confirmed_allocations, to_attr="confirmed_allocations"))
        .order_by("account_id", "period__starts", "id")
    )
    totals = {}
    meta = {}
    for charge in charges:
        paid = sum((allocation.amount for allocation in charge.confirmed_allocations), Decimal("0.00"))
        remaining = charge.amount - paid
        key = (charge.account_id, charge.period_id)
        totals[key] = totals.get(key, Decimal("0.00")) + remaining
        meta[key] = (charge.account, charge.period)

    rows, skipped = [], []
    for key in sorted(totals, key=lambda item: (meta[item][0].number or "", meta[item][1].starts, item)):
        amount = totals[key].quantize(Decimal("0.01"))
        account, period = meta[key]
        period_code = period.starts.strftime("%m%y")
        if amount <= 0:
            continue
        number, error = _validate_text(account.number, maximum=30, label="Лицевой счёт")
        if error:
            skipped.append(VtbDebtSkip(account.pk, period_code, error))
            continue
        name, error = _validate_text(account.contact_name, maximum=60, label="ФИО")
        if error:
            skipped.append(VtbDebtSkip(account.pk, period_code, error))
            continue
        address, error = _validate_text(account.plot, maximum=100, label="Адрес")
        if error:
            skipped.append(VtbDebtSkip(account.pk, period_code, error))
            continue
        if amount > Decimal("999999.99"):
            skipped.append(VtbDebtSkip(account.pk, period_code, "Сумма: больше 999999.99"))
            continue
        rows.append(VtbDebtExportRow(number, name, address, period_code, amount))
    return rows, skipped


def render_vtb_debt_registry(rows):
    lines = [
        ";".join((row.account_number, row.payer_name, row.address, row.period, f"{row.amount:.2f}"))
        for row in rows
    ]
    text = "\r
".join(lines)
    try:
        return text.encode("cp1251")
    except UnicodeEncodeError as error:
        raise ValueError("Реестр содержит символы вне WIN-1251; исправьте ФИО/адрес перед выгрузкой.") from error
