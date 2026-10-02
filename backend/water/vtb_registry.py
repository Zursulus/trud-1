from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal, InvalidOperation
import re


class VtbRegistryError(ValueError):
    pass


_DATE_RE = re.compile(r"^(\d{2})[-/.](\d{2})[-/.](\d{4})$")
_TIME_RE = re.compile(r"^(\d{2})[-/:](\d{2})[-/:](\d{2})$")
_PERIOD_RE = re.compile(r"^(0[1-9]|1[0-2])\d{2}$")
_UNI_RE = re.compile(r"^[A-Za-z0-9]{1,25}$")


@dataclass(frozen=True)
class VtbPaymentRow:
    paid_on: date
    paid_at: time
    instrument: int
    bank_document: str
    uni: str
    personal_account: str
    payer_name: str
    payer_address: str
    payment_period: str
    operation_amount: Decimal
    transfer_amount: Decimal
    commission_amount: Decimal
    reserve: tuple[str, ...]


@dataclass(frozen=True)
class VtbControlRow:
    row_count: int
    operation_total: Decimal
    transfer_total: Decimal
    commission_total: Decimal
    payment_order_number: str
    payment_order_date: date


@dataclass(frozen=True)
class VtbPaymentRegistry:
    rows: tuple[VtbPaymentRow, ...]
    control: VtbControlRow
    encoding: str


def _parse_date(value: str, field: str) -> date:
    match = _DATE_RE.fullmatch(value.strip())
    if not match:
        raise VtbRegistryError(f"{field}: expected DD-MM-YYYY/DD/MM/YYYY")
    day, month, year = map(int, match.groups())
    try:
        return date(year, month, day)
    except ValueError as error:
        raise VtbRegistryError(f"{field}: invalid date") from error


def _parse_time(value: str) -> time:
    match = _TIME_RE.fullmatch(value.strip())
    if not match:
        raise VtbRegistryError("payment time: expected HH-MM-SS/HH:MM:SS")
    hour, minute, second = map(int, match.groups())
    try:
        return time(hour, minute, second)
    except ValueError as error:
        raise VtbRegistryError("payment time: invalid time") from error


def _money(value: str, field: str) -> Decimal:
    try:
        amount = Decimal(value.strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as error:
        raise VtbRegistryError(f"{field}: invalid amount") from error
    if amount < 0:
        raise VtbRegistryError(f"{field}: negative amount")
    return amount


def decode_vtb_text(payload: bytes, *, encoding: str = "auto") -> tuple[str, str]:
    aliases = {"win-1251": "cp1251", "windows-1251": "cp1251", "koi8r": "koi8-r"}
    encoding = aliases.get((encoding or "auto").lower(), (encoding or "auto").lower())
    if encoding not in {"auto", "utf-8", "cp1251", "koi8-r"}:
        raise VtbRegistryError("unsupported registry encoding")
    if encoding != "auto":
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError as error:
            raise VtbRegistryError(f"registry is not valid {encoding}") from error
    if payload.startswith(b"\xef\xbb\xbf"):
        return payload.decode("utf-8-sig"), "utf-8-sig"
    try:
        return payload.decode("utf-8"), "utf-8"
    except UnicodeDecodeError as error:
        raise VtbRegistryError(
            "single-byte encoding is ambiguous; choose WIN-1251 or KOI8-R explicitly"
        ) from error


def parse_payment_registry(payload: bytes, *, encoding: str = "auto") -> VtbPaymentRegistry:
    text, encoding = decode_vtb_text(payload, encoding=encoding)
    lines = [line.strip("\r") for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise VtbRegistryError("registry must contain payment rows and one control row")
    if not lines[-1].startswith("="):
        raise VtbRegistryError("final control row is missing")
    if any(line.startswith("=") for line in lines[:-1]):
        raise VtbRegistryError("control row must be final")

    rows = []
    for number, line in enumerate(lines[:-1], start=1):
        fields = line.split(";")
        if len(fields) < 12:
            raise VtbRegistryError(f"row {number}: expected at least 12 fields")
        account = fields[5].strip()
        period = fields[8].strip()
        uni = fields[4].strip()
        if not (1 <= len(account) <= 30):
            raise VtbRegistryError(f"row {number}: invalid personal account")
        if not _PERIOD_RE.fullmatch(period):
            raise VtbRegistryError(f"row {number}: invalid payment period MMYY")
        if not _UNI_RE.fullmatch(uni):
            raise VtbRegistryError(f"row {number}: invalid UNI")
        try:
            instrument = int(fields[2])
        except ValueError as error:
            raise VtbRegistryError(f"row {number}: invalid payment instrument") from error
        if instrument not in {1, 2, 3, 4, 5}:
            raise VtbRegistryError(f"row {number}: payment instrument must be 1..5")
        bank_document = fields[3].strip()
        if not bank_document.isdigit():
            raise VtbRegistryError(f"row {number}: bank document must be numeric")
        rows.append(VtbPaymentRow(
            paid_on=_parse_date(fields[0], f"row {number} payment date"),
            paid_at=_parse_time(fields[1]),
            instrument=instrument,
            bank_document=bank_document,
            uni=uni,
            personal_account=account,
            payer_name=fields[6].strip(),
            payer_address=fields[7].strip(),
            payment_period=period,
            operation_amount=_money(fields[9], f"row {number} operation amount"),
            transfer_amount=_money(fields[10], f"row {number} transfer amount"),
            commission_amount=_money(fields[11], f"row {number} commission"),
            reserve=tuple(fields[12:]),
        ))

    control_fields = lines[-1][1:].split(";")
    if len(control_fields) != 6:
        raise VtbRegistryError("control row must contain exactly 6 fields after '='")
    try:
        row_count = int(control_fields[0])
    except ValueError as error:
        raise VtbRegistryError("control row count is invalid") from error
    control = VtbControlRow(
        row_count=row_count,
        operation_total=_money(control_fields[1], "control operation total"),
        transfer_total=_money(control_fields[2], "control transfer total"),
        commission_total=_money(control_fields[3], "control commission total"),
        payment_order_number=control_fields[4].strip(),
        payment_order_date=_parse_date(control_fields[5], "payment order date"),
    )
    if not control.payment_order_number.isdigit():
        raise VtbRegistryError("payment order number must be numeric")
    if control.row_count != len(rows):
        raise VtbRegistryError("control row count does not match payment rows")
    checks = (
        (sum((r.operation_amount for r in rows), Decimal("0.00")), control.operation_total, "operation"),
        (sum((r.transfer_amount for r in rows), Decimal("0.00")), control.transfer_total, "transfer"),
        (sum((r.commission_amount for r in rows), Decimal("0.00")), control.commission_total, "commission"),
    )
    for actual, expected, label in checks:
        if actual != expected:
            raise VtbRegistryError(f"control {label} total does not match payment rows")
    return VtbPaymentRegistry(tuple(rows), control, encoding)


def render_debt_registry(rows, *, encoding="cp1251") -> bytes:
    output = []
    for number, row in enumerate(rows, start=1):
        account = str(row["personal_account"]).strip()
        name = str(row.get("payer_name") or "").strip()
        address = str(row.get("payer_address") or "").strip()
        period = str(row["payment_period"]).strip()
        amount = _money(str(row["accrued_amount"]), f"row {number} accrued amount")
        if not (1 <= len(account) <= 30):
            raise VtbRegistryError(f"row {number}: invalid personal account")
        if len(name) > 60 or len(address) > 100:
            raise VtbRegistryError(f"row {number}: payer name/address exceeds VTB limit")
        if not _PERIOD_RE.fullmatch(period):
            raise VtbRegistryError(f"row {number}: invalid payment period MMYY")
        output.append(";".join((account, name, address, period, f"{amount:.2f}")))
    return ("\r\n".join(output)).encode(encoding)
