from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from openpyxl import load_workbook

from .package_imports import SHEETS, inspect_water_package
from .models import Account, LandPlot, Meter, Person, SupplyNode, WaterGroup


def _rows(book, name):
    ws = book[name]
    header = [str(v or '').strip() for v in next(ws.iter_rows(values_only=True))]
    for values in ws.iter_rows(values_only=True):
        if not any(v not in (None, '') for v in values):
            continue
        yield {header[i]: values[i] if i < len(values) else None for i in range(len(header))}


def _text(value):
    return str(value or '').strip()


def _append_note(current, line):
    line = _text(line)
    if not line:
        return current or ''
    return f'{current}\n{line}'.strip() if current else line


@transaction.atomic
def import_verified_water_package(uploaded_file):
    """Write only facts that the package can represent without invented dates.

    Time-bounded PlotRelation/Membership records are deliberately not created here
    unless their required dates are actually present in the package. Undated meter
    values are preserved verbatim in Meter.notes and never turned into Reading rows.
    """
    uploaded_file.seek(0)
    report = inspect_water_package(uploaded_file)
    if not report.get('structure_ready') or report.get('blocking_issues'):
        raise ValidationError('Пакет не прошёл предварительную проверку.')
    uploaded_file.seek(0)
    book = load_workbook(uploaded_file, read_only=True, data_only=True)

    # First production import is intentionally empty-database only. This prevents
    # silent merges/overwrites and makes retry semantics explicit.
    guarded = (Account, LandPlot, Person, SupplyNode, WaterGroup, Meter)
    occupied = {m.__name__: m.objects.count() for m in guarded if m.objects.exists()}
    if occupied:
        raise ValidationError(f'Импорт разрешён только в пустую базу water: {occupied}')

    accounts = {}
    plots = {}
    for row in _rows(book, 'Участки'):
        plot_key = _text(row.get('plot_key'))
        label = _text(row.get('plot_label')) or _text(row.get('address')) or plot_key
        address = _text(row.get('address'))
        account = Account.objects.create(
            number=plot_key,
            plot=label,
            contact_name=_text(row.get('person_name')),
            phone=_text(row.get('phone')),
            notes=_append_note('', f"Источник: {_text(row.get('source'))}"),
        )
        plot = LandPlot.objects.create(label=label, address=address, account=account)
        accounts[plot_key] = account
        plots[plot_key] = plot

    people = {}
    for row in _rows(book, 'Люди'):
        key = _text(row.get('person_key'))
        people[key] = Person.objects.create(
            full_name=_text(row.get('full_name')),
            phone=_text(row.get('phone')),
            notes=_text(row.get('notes')),
        )

    nodes = {}
    for row in _rows(book, 'Узлы'):
        name = _text(row.get('name'))
        nodes[name] = SupplyNode.objects.create(name=name, notes=_text(row.get('notes')))

    groups = {}
    for row in _rows(book, 'Группы'):
        name = _text(row.get('name'))
        node_name = _text(row.get('node'))
        groups[name] = WaterGroup.objects.create(
            name=name,
            node=nodes[node_name],
            notes=_append_note(_text(row.get('notes')), f"Тип из источника: {_text(row.get('kind'))}"),
        )

    meters = {}
    meter_rows = list(_rows(book, 'Индивидуальные счетчики')) + list(_rows(book, 'Общие и контрольные'))
    for row in meter_rows:
        key = _text(row.get('meter_key'))
        plot_key = _text(row.get('plot_key'))
        group_name = _text(row.get('group'))
        node_name = _text(row.get('node'))
        kind = _text(row.get('kind')) or Meter.INDIVIDUAL
        if kind not in dict(Meter.KIND_CHOICES):
            kind = Meter.OTHER
        meter = Meter.objects.create(
            name=key,
            kind=kind,
            serial=_text(row.get('serial')),
            account=accounts.get(plot_key),
            group=groups.get(group_name),
            node=nodes.get(node_name),
            notes=_text(row.get('notes')),
        )
        meters[key] = meter

    preserved = 0
    skipped = 0
    dated_deferred = 0
    for row in _rows(book, 'Показания'):
        meter = meters[_text(row.get('meter_key'))]
        raw_date = row.get('date')
        raw_value = row.get('value')
        if raw_value in (None, ''):
            skipped += 1
            continue
        if raw_date in (None, ''):
            meter.notes = _append_note(
                meter.notes,
                f"Недатированное исходное показание: {_text(raw_value)}; источник: {_text(row.get('source'))}; строка источника: {_text(row.get('source_row'))}",
            )
            meter.save(update_fields=['notes'])
            preserved += 1
            continue
        # Reading requires a trustworthy date plus production semantics (period,
        # chronology/rollover checks). Preserve the fact but defer creation.
        meter.notes = _append_note(
            meter.notes,
            f"Датированное исходное показание (не импортировано как Reading): {_text(raw_date)} = {_text(raw_value)}; источник: {_text(row.get('source'))}",
        )
        meter.save(update_fields=['notes'])
        dated_deferred += 1

    result = {
        'accounts': Account.objects.count(),
        'plots': LandPlot.objects.count(),
        'people': Person.objects.count(),
        'nodes': SupplyNode.objects.count(),
        'groups': WaterGroup.objects.count(),
        'meters': Meter.objects.count(),
        'undated_values_preserved': preserved,
        'empty_values_skipped': skipped,
        'dated_values_deferred': dated_deferred,
        'plot_relations_created': 0,
        'memberships_created': 0,
    }
    return result
