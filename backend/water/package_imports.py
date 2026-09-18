"""Validation for the multi-sheet water import workbook.

This module deliberately performs no writes to the working registry. It is the
first gate for the staged import package prepared from the legacy water file.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation
import io
import zipfile

from django.core.exceptions import ValidationError

MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE = 50 * 1024 * 1024
MAX_ROWS_PER_SHEET = 5000

SHEETS = {
    'Участки': ('plot_id', 'label', 'address', 'cadastral_number', 'contact_name', 'phone', 'source_sheet', 'source_row', 'status'),
    'Люди': ('person_id', 'full_name', 'phone', 'plot_id', 'role', 'notes'),
    'Узлы': ('node_name', 'notes'),
    'Группы': ('group_name', 'node_name', 'source', 'notes'),
    'Состав групп': ('plot_id', 'group_name', 'starts', 'ends', 'notes'),
    'Индивидуальные счетчики': ('meter_id', 'kind', 'node_name', 'group_name', 'plot_id', 'serial', 'notes'),
    'Общие и контрольные': ('meter_id', 'kind', 'node_name', 'group_name', 'serial', 'source_text', 'source_sheet', 'source_row', 'status'),
    'Показания': ('meter_id', 'date', 'value_m3', 'notes', 'source_sheet', 'source_row'),
}


def _text(value):
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _rows(sheet):
    values = sheet.iter_rows(values_only=True)
    try:
        header = tuple(_text(value) for value in next(values))
    except StopIteration:
        return (), []
    rows = []
    for number, values in enumerate(values, 2):
        if number > MAX_ROWS_PER_SHEET + 1:
            raise ValidationError(f'{sheet.title}: больше {MAX_ROWS_PER_SHEET} строк.')
        if not any(value not in (None, '') for value in values):
            continue
        normalized = tuple(_text(value) for value in values)
        # Prepared workbooks may repeat a service header inside a table.
        # Recognize it by the complete column signature rather than by position.
        if normalized[:len(header)] == header:
            continue
        rows.append((number, dict(zip(header, normalized))))
    return header, rows


def inspect_water_package(upload):
    """Return a dry-run report for the prepared workbook; never writes models."""
    data = upload.read(MAX_FILE_SIZE + 1)
    if len(data) > MAX_FILE_SIZE:
        raise ValidationError('Файл больше 5 МБ.')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_UNCOMPRESSED_SIZE:
                raise ValidationError('Распакованный Excel слишком велик.')
    except zipfile.BadZipFile as error:
        raise ValidationError('Файл не является корректным XLSX.') from error

    from openpyxl import load_workbook
    try:
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    except Exception as error:
        raise ValidationError('Не удалось безопасно прочитать XLSX.') from error

    missing = [name for name in SHEETS if name not in workbook.sheetnames]
    if missing:
        workbook.close()
        raise ValidationError('Нет обязательных листов: ' + ', '.join(missing))

    parsed = {}
    blocking_issues = []
    review_notes = []
    counts = {}
    for name, required in SHEETS.items():
        header, rows = _rows(workbook[name])
        absent = [column for column in required if column not in header]
        if absent:
            blocking_issues.append(f'{name}: нет колонок {", ".join(absent)}.')
        parsed[name] = rows
        counts[name] = len(rows)
    workbook.close()

    def values(sheet, field):
        return [row.get(field, '') for _, row in parsed[sheet] if row.get(field, '')]

    def unique(sheet, field):
        found = values(sheet, field)
        for value, count in Counter(found).items():
            if count > 1:
                blocking_issues.append(f'{sheet}: {field} «{value}» повторяется {count} раза.')
        return set(found)

    plot_ids = unique('Участки', 'plot_id')
    person_ids = unique('Люди', 'person_id')
    node_names = unique('Узлы', 'node_name')
    group_names = unique('Группы', 'group_name')
    individual_meter_ids = unique('Индивидуальные счетчики', 'meter_id')
    system_meter_ids = unique('Общие и контрольные', 'meter_id')
    meter_ids = individual_meter_ids | system_meter_ids
    if individual_meter_ids & system_meter_ids:
        blocking_issues.append('Одинаковый meter_id встречается в индивидуальных и общих счётчиках.')

    for number, row in parsed['Люди']:
        if row.get('plot_id') and row['plot_id'] not in plot_ids:
            blocking_issues.append(f'Люди строка {number}: неизвестный plot_id {row["plot_id"]}.')
    for number, row in parsed['Группы']:
        if row.get('node_name') not in node_names:
            blocking_issues.append(f'Группы строка {number}: неизвестный узел {row.get("node_name") or "(пусто)"}.')
    for number, row in parsed['Состав групп']:
        if row.get('plot_id') not in plot_ids:
            blocking_issues.append(f'Состав групп строка {number}: неизвестный plot_id {row.get("plot_id") or "(пусто)"}.')
        if row.get('group_name') not in group_names:
            blocking_issues.append(f'Состав групп строка {number}: неизвестная группа {row.get("group_name") or "(пусто)"}.')

    allowed_kinds = {'main', 'line', 'individual', 'irrigation'}
    for sheet in ('Индивидуальные счетчики', 'Общие и контрольные'):
        for number, row in parsed[sheet]:
            kind = row.get('kind')
            if kind not in allowed_kinds:
                blocking_issues.append(f'{sheet} строка {number}: неизвестный kind {kind or "(пусто)"}.')
            if row.get('node_name') not in node_names:
                blocking_issues.append(f'{sheet} строка {number}: неизвестный узел {row.get("node_name") or "(пусто)"}.')
            if kind == 'individual' and row.get('plot_id') not in plot_ids:
                blocking_issues.append(f'{sheet} строка {number}: неизвестный plot_id {row.get("plot_id") or "(пусто)"}.')
            if kind == 'line' and row.get('group_name') not in group_names:
                blocking_issues.append(f'{sheet} строка {number}: для линейного счётчика нужна существующая группа.')
            if kind in {'main', 'irrigation'} and row.get('group_name'):
                blocking_issues.append(f'{sheet} строка {number}: общий/поливочный счётчик не должен иметь группу.')

    reading_keys = set()
    dated_readings = 0
    undated_values = 0
    empty_readings = 0
    for number, row in parsed['Показания']:
        meter_id = row.get('meter_id')
        if meter_id not in meter_ids:
            blocking_issues.append(f'Показания строка {number}: неизвестный meter_id {meter_id or "(пусто)"}.')

        raw_value = row.get('value_m3', '')
        value = None
        if raw_value:
            try:
                value = Decimal(raw_value.replace(',', '.'))
                if value < 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                blocking_issues.append(f'Показания строка {number}: некорректное значение {raw_value}.')
        else:
            empty_readings += 1
            review_notes.append(
                f'Показания строка {number}: значение отсутствует в источнике; показание не создаётся.'
            )

        reading_date = row.get('date')
        if not reading_date:
            if value is not None:
                undated_values += 1
                source = row.get('source_sheet') or 'не указан'
                source_row = row.get('source_row') or 'не указана'
                review_notes.append(
                    f'Показания строка {number}: {value} м³ без достоверной даты; '
                    f'сохранить как недатированное исходное значение в примечании счётчика '
                    f'(источник: {source}, строка {source_row}), Reading не создавать.'
                )
            continue

        if value is None:
            continue
        dated_readings += 1
        key = (meter_id, reading_date)
        if key in reading_keys:
            blocking_issues.append(f'Показания строка {number}: повтор meter_id + date.')
        reading_keys.add(key)

    issues = blocking_issues + review_notes
    return {
        'counts': counts,
        'issues': issues,
        'blocking_issues': blocking_issues,
        'review_notes': review_notes,
        'ready': not blocking_issues,
        'structure_ready': not blocking_issues,
        'reading_plan': {
            'dated': dated_readings,
            'undated_to_meter_notes': undated_values,
            'empty_skipped': empty_readings,
        },
        'summary': (
            f'Участков: {len(plot_ids)}; людей: {len(person_ids)}; узлов: {len(node_names)}; '
            f'групп: {len(group_names)}; счётчиков: {len(meter_ids)}; '
            f'строк показаний: {counts["Показания"]}; блокирующих ошибок: {len(blocking_issues)}; '
            f'требуют сохранения/проверки: {len(review_notes)}.'
        ),
    }
