from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q
from openpyxl import load_workbook

from water.models import Account, Membership, Meter, Reading, SupplyNode, WaterGroup
from water.package_imports import inspect_water_package


CONFIRM = 'IMPORT-DATED-WATER-READINGS'


def _text(value):
    return str(value or '').strip()


def _append_note(current, line):
    current = _text(current)
    line = _text(line)
    if not line or line in current:
        return current
    return f'{current}\n{line}'.strip() if current else line


def _parse_date(value, *, allow_blank=False):
    if value in (None, ''):
        if allow_blank:
            return None
        raise CommandError('Пустая дата там, где она обязательна.')
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    for parser in (
        lambda s: date.fromisoformat(s),
        lambda s: datetime.strptime(s, '%d.%m.%Y').date(),
    ):
        try:
            return parser(text)
        except (ValueError, TypeError):
            pass
    raise CommandError(f'Не удалось распознать дату: {text or "(пусто)"}')


def _rows(sheet):
    values = sheet.iter_rows(values_only=True)
    try:
        header = tuple(_text(v) for v in next(values))
    except StopIteration:
        return
    for values in values:
        if not any(v not in (None, '') for v in values):
            continue
        normalized = tuple(_text(v) for v in values)
        if normalized[:len(header)] == header:
            continue
        yield {header[i]: values[i] if i < len(values) else None for i in range(len(header))}


def _import_meter_id(meter):
    marker = 'ID импорта:'
    for line in _text(meter.notes).splitlines():
        if marker in line:
            return line.split(marker, 1)[1].strip().split(';', 1)[0].strip()
    return ''


def _overlap(starts_a, ends_a, starts_b, ends_b):
    far = date.max
    return starts_a < (ends_b or far) and starts_b < (ends_a or far)


class Command(BaseCommand):
    help = (
        'Безопасно сверить и дополнить существующий water-реестр из проверенного XLSX-пакета: '
        'источники групп, состав групп, исторические приборы и датированные показания. '
        'Существующие ручные данные никогда не перезаписываются.'
    )

    def add_arguments(self, parser):
        parser.add_argument('file', type=Path)
        parser.add_argument('--apply', action='store_true', help='Выполнить запись. Без флага только dry-run.')
        parser.add_argument('--confirm', default='')

    def handle(self, *args, **options):
        path = options['file'].resolve()
        if not path.is_file():
            raise CommandError(f'Файл не найден: {path}')

        raw = path.read_bytes()
        upload = SimpleUploadedFile(path.name, raw)
        report = inspect_water_package(upload)
        if report.get('blocking_issues'):
            raise CommandError('Пакет не прошёл проверку: ' + '; '.join(report['blocking_issues']))

        book = load_workbook(path, read_only=True, data_only=True)
        conflicts = []

        nodes = {node.name: node for node in SupplyNode.objects.all()}
        groups = {group.name: group for group in WaterGroup.objects.all()}
        accounts = {account.number: account for account in Account.objects.exclude(number__isnull=True)}

        group_updates = []
        for row in _rows(book['Группы']):
            name = _text(row.get('group_name'))
            desired = _text(row.get('source')) or 'unknown'
            group = groups.get(name)
            if group is None:
                conflicts.append(f'Группа «{name}» отсутствует в рабочей базе.')
                continue
            if desired == 'unknown' or group.source == desired:
                continue
            if group.source != 'unknown':
                conflicts.append(
                    f'Группа «{name}»: в базе source={group.source}, в пакете source={desired}; '
                    'существующее решение автоматически не меняется.'
                )
                continue
            group_updates.append((group, desired))

        membership_creates = []
        membership_same = 0
        for row in _rows(book['Состав групп']):
            plot_id = _text(row.get('plot_id'))
            group_name = _text(row.get('group_name'))
            starts = _parse_date(row.get('starts'))
            ends = _parse_date(row.get('ends'), allow_blank=True)
            account = accounts.get(plot_id)
            group = groups.get(group_name)
            if account is None:
                conflicts.append(f'{plot_id}: лицевой счёт/участок не найден в рабочей базе.')
                continue
            if group is None:
                conflicts.append(f'{plot_id}: группа «{group_name}» не найдена.')
                continue
            exact = Membership.objects.filter(
                account=account, group=group, starts=starts, ends=ends,
            ).first()
            if exact:
                membership_same += 1
                continue
            existing = list(Membership.objects.filter(account=account).select_related('group'))
            overlapping = [m for m in existing if _overlap(starts, ends, m.starts, m.ends)]
            if overlapping:
                current = ', '.join(f'{m.group.name} {m.starts}–{m.ends or "…"}' for m in overlapping)
                conflicts.append(
                    f'{plot_id}: пакет просит {group_name} {starts}–{ends or "…"}, '
                    f'но уже есть пересекающееся членство: {current}.'
                )
                continue
            membership_creates.append((account, group, starts, ends))

        package_meter_rows = {}
        for sheet_name in ('Индивидуальные счетчики', 'Общие и контрольные'):
            for row in _rows(book[sheet_name]):
                meter_id = _text(row.get('meter_id'))
                if meter_id:
                    package_meter_rows[meter_id] = row

        db_meter_map = {}
        duplicate_meter_ids = []
        for meter in Meter.objects.select_related('node', 'group', 'account').all():
            meter_id = _import_meter_id(meter)
            if not meter_id:
                continue
            if meter_id in db_meter_map:
                duplicate_meter_ids.append(meter_id)
            db_meter_map[meter_id] = meter
        if duplicate_meter_ids:
            conflicts.append('Повторяющиеся ID импорта счётчиков в базе: ' + ', '.join(sorted(set(duplicate_meter_ids))))

        meter_creates = []
        meter_lifecycle_updates = []
        planned_meter_ids = set(db_meter_map)
        for meter_id, row in package_meter_rows.items():
            commissioned_on = _parse_date(row.get('commissioned_on'), allow_blank=True)
            retired_on = _parse_date(row.get('retired_on'), allow_blank=True)
            existing = db_meter_map.get(meter_id)
            if existing is None:
                # Only explicitly historical split meters may be created into a live registry.
                if not meter_id.endswith('-H1'):
                    conflicts.append(f'{meter_id}: счётчик отсутствует в рабочей базе; автоматическое создание запрещено.')
                    continue
                plot_id = _text(row.get('plot_id'))
                node_name = _text(row.get('node_name'))
                group_name = _text(row.get('group_name'))
                account = accounts.get(plot_id)
                node = nodes.get(node_name)
                group = groups.get(group_name) if group_name else None
                if account is None or node is None:
                    conflicts.append(f'{meter_id}: не удалось найти участок/узел для исторического прибора.')
                    continue
                kind = _text(row.get('kind')) or 'individual'
                serial = _text(row.get('serial')) or meter_id
                notes = _append_note(_text(row.get('notes')), f'ID импорта: {meter_id}')
                meter_creates.append({
                    'meter_id': meter_id, 'serial': serial, 'kind': kind, 'node': node,
                    'group': group, 'account': account, 'commissioned_on': commissioned_on,
                    'retired_on': retired_on, 'notes': notes,
                })
                planned_meter_ids.add(meter_id)
                continue

            changed = {}
            if commissioned_on and existing.commissioned_on != commissioned_on:
                if existing.commissioned_on is not None:
                    conflicts.append(
                        f'{meter_id}: commissioned_on уже {existing.commissioned_on}, пакет предлагает {commissioned_on}.'
                    )
                elif Reading.objects.filter(meter=existing, date__lt=commissioned_on).exists():
                    conflicts.append(
                        f'{meter_id}: нельзя поставить commissioned_on={commissioned_on}: '
                        'в базе уже есть более ранние ручные показания.'
                    )
                else:
                    changed['commissioned_on'] = commissioned_on
            if retired_on and existing.retired_on != retired_on:
                if existing.retired_on is not None:
                    conflicts.append(
                        f'{meter_id}: retired_on уже {existing.retired_on}, пакет предлагает {retired_on}.'
                    )
                elif Reading.objects.filter(meter=existing, date__gt=retired_on).exists():
                    conflicts.append(
                        f'{meter_id}: нельзя поставить retired_on={retired_on}: '
                        'в базе уже есть более поздние ручные показания.'
                    )
                else:
                    changed['retired_on'] = retired_on
            if changed:
                meter_lifecycle_updates.append((existing, changed))

        readings_by_meter = {}
        reading_rows = []
        for row in _rows(book['Показания']):
            raw_date = row.get('date')
            raw_value = row.get('value_m3')
            if raw_date in (None, '') or raw_value in (None, ''):
                continue
            meter_id = _text(row.get('meter_id'))
            if meter_id not in planned_meter_ids:
                conflicts.append(f'{meter_id}: нет однозначного прибора для показания.')
                continue
            reading_date = _parse_date(raw_date)
            try:
                value = Decimal(str(raw_value).replace(',', '.'))
            except (InvalidOperation, ValueError) as error:
                book.close()
                raise CommandError(f'{meter_id} {reading_date}: некорректное значение {raw_value}') from error
            item = (reading_date, value, row)
            readings_by_meter.setdefault(meter_id, []).append(item)
            reading_rows.append((meter_id, reading_date, value, row))

        # Validate package readings against all existing manual readings without changing any of them.
        to_create = []
        already_same = 0
        for meter_id, package_values in readings_by_meter.items():
            meter = db_meter_map.get(meter_id)
            existing_values = []
            if meter is not None:
                existing_values = list(Reading.objects.filter(meter=meter).order_by('date', 'id'))
                existing_by_date = {reading.date: reading for reading in existing_values}
            else:
                existing_by_date = {}

            combined = [(reading.date, reading.value, 'база') for reading in existing_values]
            for reading_date, value, row in package_values:
                existing = existing_by_date.get(reading_date)
                if existing:
                    if existing.value == value:
                        already_same += 1
                    else:
                        conflicts.append(
                            f'{meter_id} {reading_date}: ручное/существующее значение {existing.value}, '
                            f'в пакете {value}; существующая запись имеет приоритет.'
                        )
                    continue
                combined.append((reading_date, value, 'пакет'))
                to_create.append((meter_id, reading_date, value, row))

            combined.sort(key=lambda item: item[0])
            previous = None
            for reading_date, value, origin in combined:
                if previous and value < previous[1]:
                    conflicts.append(
                        f'{meter_id}: последовательность ломается между {previous[0]}={previous[1]} '
                        f'и {reading_date}={value} ({origin}).'
                    )
                previous = (reading_date, value)

        book.close()

        if conflicts:
            unique = []
            seen = set()
            for conflict in conflicts:
                if conflict not in seen:
                    seen.add(conflict)
                    unique.append(conflict)
            raise CommandError(
                f'DRY-RUN/импорт остановлен: найдено конфликтов {len(unique)}. '
                + ' | '.join(unique[:30])
            )

        self.stdout.write(f'Пакет валиден. Показаний в пакете: {len(reading_rows)}')
        self.stdout.write(f'Новых показаний: {len(to_create)}; уже совпадают: {already_same}')
        self.stdout.write(f'Новых исторических счётчиков: {len(meter_creates)}')
        self.stdout.write(f'Обновлений дат жизни счётчиков: {len(meter_lifecycle_updates)}')
        self.stdout.write(f'Новых Membership: {len(membership_creates)}; уже совпадают: {membership_same}')
        self.stdout.write(f'Источников групп к установке: {len(group_updates)}')

        if not options['apply']:
            self.stdout.write(self.style.WARNING(
                f'DRY-RUN: база не изменена. Для записи добавьте --apply --confirm {CONFIRM}'
            ))
            return
        if options['confirm'] != CONFIRM:
            raise CommandError(f'Для записи нужен --confirm {CONFIRM}')

        created_readings = 0
        try:
            with transaction.atomic():
                for group, desired in group_updates:
                    group.source = desired
                    group._change_reason = 'Импорт: источник расхода группы из проверенного пакета'
                    group.save(update_fields=['source'])

                for account, group, starts, ends in membership_creates:
                    membership = Membership(account=account, group=group, starts=starts, ends=ends)
                    membership._change_reason = 'Импорт состава групп из проверенного пакета'
                    membership.save()

                for spec in meter_creates:
                    meter = Meter.objects.create(
                        serial=spec['serial'], kind=spec['kind'], node=spec['node'],
                        group=spec['group'], account=spec['account'],
                        commissioned_on=spec['commissioned_on'], retired_on=spec['retired_on'],
                        notes=spec['notes'],
                    )
                    db_meter_map[spec['meter_id']] = meter

                for meter, changed in meter_lifecycle_updates:
                    for field, value in changed.items():
                        setattr(meter, field, value)
                    meter._change_reason = 'Импорт: границы жизни прибора из проверенного пакета'
                    meter.save(update_fields=list(changed))

                for meter_id, reading_date, value, row in sorted(
                    to_create, key=lambda item: (item[0], item[1], item[2])
                ):
                    meter = db_meter_map[meter_id]
                    source = _text(row.get('source_sheet'))
                    source_row = _text(row.get('source_row'))
                    original_note = _text(row.get('notes'))
                    note = f'Импорт из подготовленного пакета; источник: {source}; строка: {source_row}'
                    if original_note:
                        note += f'; {original_note}'
                    reading = Reading(meter=meter, date=reading_date, value=value, notes=note)
                    reading._change_reason = 'Импорт исторического показания из проверенного пакета'
                    reading.save()
                    created_readings += 1
        except ValidationError as error:
            raise CommandError(f'Импорт отменён целиком: {error}') from error

        self.stdout.write(self.style.SUCCESS(
            'Импорт завершён: '
            f'показаний {created_readings}, исторических счётчиков {len(meter_creates)}, '
            f'Membership {len(membership_creates)}, источников групп {len(group_updates)}.'
        ))
