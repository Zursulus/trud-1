from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import csv
import io


class VtbRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class VtbDebtRow:
    account_number: str
    payer_name: str
    address: str
    period: str
    amount: Decimal


@dataclass(frozen=True)
class VtbPaymentRow:
    paid_at: datetime
    instrument: int
    bank_document: str
    operation_id: str
    account_number: str
    payer_name: str
    address: str
    period: str
    operation_amount: Decimal
    transfer_amount: Decimal
    commission_amount: Decimal


@dataclass(frozen=True)
class VtbPaymentControl:
    row_count: int
    operation_total: Decimal
    transfer_total: Decimal
    commission_total: Decimal
    payment_order: str
    payment_order_date: str


def _decimal(raw, field):
    try:
        value = Decimal(raw)
    except (InvalidOperation, TypeError):
        raise VtbRegistryError(f"{field}: invalid decimal")
    if value < 0:
        raise VtbRegistryError(f"{field}: negative value")
    return value.quantize(Decimal("0.01"))


def _period(raw):
    value = (raw or "").strip()
    if len(value) != 4 or not value.isdigit():
        raise VtbRegistryError("period: expected MMYY")
    month = int(value[:2])
    if not 1 <= month <= 12:
        raise VtbRegistryError("period: invalid month")
    return value


def _rows(text):
    return list(csv.reader(io.StringIO(text), delimiter=";"))


def parse_debt_registry(text):
    result = []
    for line_no, row in enumerate(_rows(text), 1):
        if not row or all(not cell.strip() for cell in row):
            continue
        if len(row) != 5:
            raise VtbRegistryError(f"line {line_no}: expected 5 fields")
        account, name, address, period, amount = (cell.strip() for cell in row)
        if not 1 <= len(account) <= 30:
            raise VtbRegistryError(f"line {line_no}: account length")
        result.append(VtbDebtRow(account, name, address, _period(period), _decimal(amount, "amount")))
    return result


def parse_payment_registry(text):
    payments = []
    control = None
    for line_no, row in enumerate(_rows(text), 1):
        if not row or all(not cell.strip() for cell in row):
            continue
        if row[0].startswith("="):
            if control is not None:
                raise VtbRegistryError("duplicate control row")
            values = [row[0][1:].strip(), *[cell.strip() for cell in row[1:]]]
            if len(values) != 6:
                raise VtbRegistryError(f"line {line_no}: expected 6 control fields")
            try:
                count = int(values[0])
            except ValueError:
                raise VtbRegistryError("control: invalid row count")
            control = VtbPaymentControl(
                count,
                _decimal(values[1], "control operation total"),
                _decimal(values[2], "control transfer total"),
                _decimal(values[3], "control commission total"),
                values[4],
                values[5],
            )
            continue
        if control is not None:
            raise VtbRegistryError("data row after control row")
        if len(row) < 12:
            raise VtbRegistryError(f"line {line_no}: expected at least 12 fields")
        values = [cell.strip() for cell in row[:12]]
        try:
            paid_at = datetime.strptime(values[0] + " " + values[1], "%d-%m-%Y %H-%M-%S")
            instrument = int(values[2])
        except ValueError:
            raise VtbRegistryError(f"line {line_no}: invalid date/time/instrument")
        if instrument not in {1, 2, 3, 4, 5}:
            raise VtbRegistryError(f"line {line_no}: invalid instrument")
        account = values[5]
        if not 1 <= len(account) <= 30:
            raise VtbRegistryError(f"line {line_no}: account length")
        payments.append(VtbPaymentRow(
            paid_at, instrument, values[3], values[4], account, values[6], values[7],
            _period(values[8]), _decimal(values[9], "operation amount"),
            _decimal(values[10], "transfer amount"), _decimal(values[11], "commission amount"),
        ))
    if control is None:
        raise VtbRegistryError("missing control row")
    if control.row_count != len(payments):
        raise VtbRegistryError("control: row count mismatch")
    if control.operation_total != sum((p.operation_amount for p in payments), Decimal("0.00")):
        raise VtbRegistryError("control: operation total mismatch")
    if control.transfer_total != sum((p.transfer_amount for p in payments), Decimal("0.00")):
        raise VtbRegistryError("control: transfer total mismatch")
    if control.commission_total != sum((p.commission_amount for p in payments), Decimal("0.00")):
        raise VtbRegistryError("control: commission total mismatch")
    return payments, control


def match_accounts(account_numbers, accounts):
    """Read-only exact matching. Never infer by name/address."""
    by_number = {}
    for account in accounts:
        number = (getattr(account, "number", "") or "").strip()
        if number:
            by_number.setdefault(number, []).append(account)
    result = []
    for number in account_numbers:
        matches = by_number.get(number, [])
        state = "matched" if len(matches) == 1 else "missing" if not matches else "ambiguous"
        result.append((number, state, matches[0] if len(matches) == 1 else None))
    return result
