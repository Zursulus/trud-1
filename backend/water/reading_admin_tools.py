from decimal import Decimal
from io import BytesIO

from django import forms
from django.contrib import admin, messages
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter

from .models import ControllerReadingSubmission, Meter, Reading


REVIEW_THRESHOLD_M3 = Decimal('100')

XLSX_HEADERS = [
    'Reading ID', 'Import / meter ID', 'Счётчик', 'Назначение',
    'Лицевой счёт', 'Участок / адрес', 'Группа', 'Узел',
    'Дата', 'Показание, м³', 'Предыдущее показание, м³', 'Дата предыдущего',
    'Расход от предыдущего, м³', 'Статус проверки', 'Причина проверки',
    'Источник', 'Примечание', 'Открыть в админке', 'Исправить привязку',
]


def _import_meter_id(meter):
    marker = 'ID импорта:'
    for line in (meter.notes or '').splitlines():
        if marker in line:
            return line.split(marker, 1)[1].strip().split(';', 1)[0].strip()
    return ''


def _xlsx_safe(value):
    if value is None:
        return ''
    text = str(value)
    if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')):
        return "'" + text
    return value


def _reading_source(reading, submissions=None):
    submissions = list(submissions if submissions is not None else reading.controller_submissions.all())
    if submissions:
        parts = []
        for item in submissions:
            actor = item.submitted_by.get_full_name() or item.submitted_by.username
            parts.append(f'Контролёр: {actor}; статус: {item.get_status_display()}')
        return ' | '.join(parts)
    if 'техническая дата по согласованной схеме' in (reading.notes or '').casefold():
        return 'Исторический импорт с технической датой'
    return 'Ручная / административная запись'


def _audit_readings():
    """Build one deterministic, read-only audit snapshot for UI and XLSX."""
    queryset = Reading.objects.select_related(
        'meter', 'meter__account', 'meter__group', 'meter__node',
    ).prefetch_related(
        'controller_submissions__submitted_by',
    ).order_by('meter_id', 'date', 'id')

    previous_by_meter = {}
    rows = []
    for reading in queryset:
        previous = previous_by_meter.get(reading.meter_id)
        consumption = reading.value - previous.value if previous is not None else None
        previous_by_meter[reading.meter_id] = reading

        submissions = list(reading.controller_submissions.all())
        reasons = []
        severity = 'ok'

        if consumption is not None and consumption < 0:
            reasons.append('Показание меньше предыдущего')
            severity = 'critical'
        if (
            reading.meter.kind == 'individual'
            and consumption is not None
            and consumption > REVIEW_THRESHOLD_M3
        ):
            reasons.append(f'Расход больше {REVIEW_THRESHOLD_M3} м³')
            severity = 'review' if severity == 'ok' else severity
        if reading.meter.commissioned_on and reading.date < reading.meter.commissioned_on:
            reasons.append('Показание раньше даты установки счётчика')
            severity = 'critical'
        if reading.meter.retired_on and reading.date > reading.meter.retired_on:
            reasons.append('Показание позже даты снятия счётчика')
            severity = 'critical'
        if any(item.meter_id != reading.meter_id for item in submissions):
            reasons.append('Связанная заявка контролёра указывает на другой счётчик')
            severity = 'critical'

        account = reading.meter.account
        import_id = _import_meter_id(reading.meter)
        source = _reading_source(reading, submissions)
        rows.append({
            'reading': reading,
            'previous': previous,
            'consumption': consumption,
            'account': account,
            'import_id': import_id,
            'source': source,
            'controller_count': len(submissions),
            'review': bool(reasons),
            'severity': severity,
            'review_reason': '; '.join(reasons),
            'change_url': reverse('admin:water_reading_change', args=[reading.pk]),
            'reassign_url': reverse('water_reading_reassign', args=[reading.pk]),
        })

    rows.sort(key=lambda row: (
        (row['account'].plot if row['account'] else '') or '',
        row['import_id'] or row['reading'].meter.serial,
        row['reading'].date,
        row['reading'].pk,
    ))
    return rows


def _style_data_sheet(ws, table_name):
    ws.freeze_panes = 'A2'
    ws.sheet_view.showGridLines = False
    ws.row_dimensions[1].height = 34
    for cell in ws[1]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='305496')
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

    widths = [
        12, 27, 24, 18, 20, 28, 24, 22, 14, 18,
        23, 17, 24, 18, 42, 42, 60, 21, 23,
    ]
    for index, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(index)].width = width

    for row in ws.iter_rows(min_row=2):
        row[8].number_format = 'dd.mm.yyyy'
        row[11].number_format = 'dd.mm.yyyy'
        row[9].number_format = '0.000'
        row[10].number_format = '0.000'
        row[12].number_format = '0.000'
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=cell.column >= 14)

        status = row[13].value
        if status == 'ПРОВЕРИТЬ':
            for cell in row:
                cell.fill = PatternFill('solid', fgColor='FFF2CC')
        elif status == 'КРИТИЧНО':
            for cell in row:
                cell.fill = PatternFill('solid', fgColor='F4CCCC')

    if ws.max_row > 1:
        table = Table(displayName=table_name, ref=f'A1:{get_column_letter(ws.max_column)}{ws.max_row}')
        table.tableStyleInfo = TableStyleInfo(
            name='TableStyleMedium2', showFirstColumn=False, showLastColumn=False,
            showRowStripes=True, showColumnStripes=False,
        )
        ws.add_table(table)
    else:
        ws.auto_filter.ref = f'A1:{get_column_letter(ws.max_column)}1'


def _append_xlsx_row(ws, row, request):
    reading = row['reading']
    previous = row['previous']
    account = row['account']
    status = 'КРИТИЧНО' if row['severity'] == 'critical' else 'ПРОВЕРИТЬ' if row['review'] else 'OK'
    values = [
        reading.pk,
        row['import_id'],
        reading.meter.serial,
        reading.meter.get_kind_display(),
        account.number if account else '',
        account.plot if account else '',
        reading.meter.group.name if reading.meter.group else '',
        reading.meter.node.name,
        reading.date,
        reading.value,
        previous.value if previous else '',
        previous.date if previous else '',
        row['consumption'] if row['consumption'] is not None else '',
        status,
        row['review_reason'],
        row['source'],
        reading.notes,
        'Открыть',
        'Исправить' if request.user.is_superuser else '',
    ]
    ws.append([_xlsx_safe(value) for value in values])
    current = ws.max_row

    open_cell = ws.cell(current, 18)
    open_cell.hyperlink = request.build_absolute_uri(row['change_url'])
    open_cell.style = 'Hyperlink'
    if request.user.is_superuser:
        fix_cell = ws.cell(current, 19)
        fix_cell.hyperlink = request.build_absolute_uri(row['reassign_url'])
        fix_cell.style = 'Hyperlink'


def export_readings_xlsx(request):
    if not request.user.has_perm('water.export_reading'):
        raise PermissionDenied

    rows = _audit_readings()
    flagged = [row for row in rows if row['review']]
    controller_count = sum(1 for row in rows if row['controller_count'])
    imported_count = sum(
        1 for row in rows if row['source'] == 'Исторический импорт с технической датой'
    )

    wb = Workbook()
    summary = wb.active
    summary.title = 'Сводка'
    summary.sheet_view.showGridLines = False
    summary.merge_cells('A1:D1')
    summary['A1'] = 'Проверка показаний ТСН «ТРУД-1»'
    summary['A1'].font = Font(size=16, bold=True, color='FFFFFF')
    summary['A1'].fill = PatternFill('solid', fgColor='305496')
    summary['A1'].alignment = Alignment(vertical='center')
    summary.row_dimensions[1].height = 30
    summary.append(['Сформировано', timezone.localtime().strftime('%d.%m.%Y %H:%M'), '', ''])
    summary.append(['Всего показаний', len(rows), '', ''])
    summary.append(['Требуют проверки', len(flagged), '', ''])
    summary.append(['Связаны с контролёром', controller_count, '', ''])
    summary.append(['Исторический импорт', imported_count, '', ''])
    summary.append(['Порог контроля для индивидуального счётчика', f'> {REVIEW_THRESHOLD_M3} м³', '', ''])
    summary.append([])
    summary.append(['Как работать', 'Сначала откройте лист «Требуют проверки». Строка там не означает ошибку — это сигнал для ручной сверки.', '', ''])
    summary.append(['Исправление', 'Используйте ссылку «Исправить» в строке или отдельную кнопку в карточке показания. Перед записью система покажет «было → станет».', '', ''])
    summary.append(['Проверка в браузере', 'Открыть страницу проверки показаний', '', ''])
    summary['B11'].hyperlink = request.build_absolute_uri(reverse('water_readings_review'))
    summary['B11'].style = 'Hyperlink'
    summary.column_dimensions['A'].width = 46
    summary.column_dimensions['B'].width = 100
    for row in summary.iter_rows(min_row=2, max_col=2):
        row[0].font = Font(bold=True)
        row[0].alignment = Alignment(vertical='top', wrap_text=True)
        row[1].alignment = Alignment(vertical='top', wrap_text=True)

    review_sheet = wb.create_sheet('Требуют проверки')
    review_sheet.append(XLSX_HEADERS)
    for row in flagged:
        _append_xlsx_row(review_sheet, row, request)
    _style_data_sheet(review_sheet, 'ReadingsToReview')

    all_sheet = wb.create_sheet('Все показания')
    all_sheet.append(XLSX_HEADERS)
    for row in rows:
        _append_xlsx_row(all_sheet, row, request)
    _style_data_sheet(all_sheet, 'AllReadings')

    stream = BytesIO()
    wb.save(stream)
    stream.seek(0)

    LogEntry.objects.create(
        user=request.user,
        content_type=ContentType.objects.get_for_model(Reading),
        object_id='',
        object_repr='Выгрузка проверки показаний XLSX',
        action_flag=2,
        change_message=f'Экспорт XLSX: {len(rows)} записей; требуют проверки: {len(flagged)}',
    )

    response = HttpResponse(
        stream.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="trud-water-readings-review.xlsx"'
    response['Cache-Control'] = 'no-store'
    return response


def reading_review_view(request):
    if not request.user.has_perm('water.view_reading'):
        raise PermissionDenied

    rows = _audit_readings()
    flagged = [row for row in rows if row['review']]
    context = {
        **admin.site.each_context(request),
        'title': 'Проверка показаний счётчиков',
        'opts': Reading._meta,
        'rows': flagged,
        'total_count': len(rows),
        'review_count': len(flagged),
        'critical_count': sum(1 for row in flagged if row['severity'] == 'critical'),
        'controller_count': sum(1 for row in rows if row['controller_count']),
        'can_export': request.user.has_perm('water.export_reading'),
        'can_reassign': request.user.is_superuser,
        'threshold': REVIEW_THRESHOLD_M3,
    }
    return TemplateResponse(request, 'admin/water/reading/review.html', context)


class MeterChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, meter):
        account = meter.account
        place = account.plot if account else meter.group or meter.node
        import_id = _import_meter_id(meter)
        prefix = f'{import_id} · ' if import_id else ''
        return f'{prefix}{meter.serial} · {place}'


class ReassignReadingForm(forms.Form):
    destination_meter = MeterChoiceField(label='Правильный счётчик', queryset=Meter.objects.none())
    reason = forms.CharField(
        label='Причина исправления', min_length=3,
        widget=forms.Textarea(attrs={'rows': 3, 'placeholder': 'Например: контролёр выбрал соседний участок'}),
        help_text='Причина попадёт в историю изменений и журнал администратора.',
    )
    reading_version = forms.IntegerField(widget=forms.HiddenInput())

    def __init__(self, *args, reading, **kwargs):
        super().__init__(*args, **kwargs)
        self.reading = reading
        self.fields['reading_version'].initial = reading.version
        self.fields['destination_meter'].queryset = Meter.objects.select_related(
            'account', 'group', 'node',
        ).filter(kind=reading.meter.kind).exclude(pk=reading.meter_id).order_by(
            'account__plot', 'group__name', 'serial', 'id',
        )

    def clean(self):
        data = super().clean()
        if data.get('reading_version') != self.reading.version:
            raise forms.ValidationError('Показание уже изменилось. Обновите страницу и повторите проверку.')
        return data


def validate_reassignment(reading, destination_meter):
    if destination_meter.pk == reading.meter_id:
        raise ValidationError('Выбран тот же счётчик.')
    if destination_meter.kind != reading.meter.kind:
        raise ValidationError('Назначение исходного и нового счётчика должно совпадать.')

    candidate = Reading(
        meter=destination_meter,
        date=reading.date,
        value=reading.value,
        notes=reading.notes,
    )
    candidate.full_clean()

    previous = Reading.objects.filter(
        meter=destination_meter, date__lt=reading.date,
    ).order_by('-date', '-id').first()
    following = Reading.objects.filter(
        meter=destination_meter, date__gt=reading.date,
    ).order_by('date', 'id').first()
    return previous, following


def reassign_reading(*, reading_id, destination_meter_id, reason, actor, expected_version):
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Укажите причину исправления.')
    if not actor or not actor.is_superuser:
        raise PermissionDenied

    with transaction.atomic():
        reading = Reading.objects.select_for_update().select_related('meter').get(pk=reading_id)
        if reading.version != expected_version:
            raise ValidationError('Показание уже изменилось. Обновите страницу и повторите проверку.')
        destination = Meter.objects.select_for_update().get(pk=destination_meter_id)
        source = Meter.objects.select_for_update().get(pk=reading.meter_id)
        validate_reassignment(reading, destination)

        old_id = reading.pk
        old_meter_label = str(source)
        destination_label = str(destination)
        full_audit_reason = f'Исправление привязки: {old_meter_label} → {destination_label}. {reason}'
        history_reason = (
            f'Исправление привязки Reading #{old_id}: meter {source.pk}→{destination.pk}. {reason}'
        )[:100]

        replacement = Reading(
            meter=destination,
            date=reading.date,
            value=reading.value,
            notes=reading.notes,
        )
        replacement._history_user = actor
        replacement._change_reason = history_reason
        replacement.save()

        submissions = list(
            ControllerReadingSubmission.objects.select_for_update().filter(reading=reading)
        )
        for submission in submissions:
            submission.meter = destination
            submission.reading = replacement
            submission._history_user = actor
            submission._change_reason = history_reason
            submission.save(update_fields=['meter', 'reading'])

        reading._history_user = actor
        reading._change_reason = history_reason
        reading.delete()

        return replacement, old_id, len(submissions), old_meter_label, destination_label, full_audit_reason


def reassign_reading_view(request, reading_id):
    if not request.user.is_superuser:
        raise PermissionDenied

    reading = get_object_or_404(
        Reading.objects.select_related('meter', 'meter__account', 'meter__group', 'meter__node'),
        pk=reading_id,
    )
    form = ReassignReadingForm(request.POST or None, reading=reading)
    preview = None

    source_previous = Reading.objects.filter(
        meter=reading.meter, date__lt=reading.date,
    ).order_by('-date', '-id').first()
    source_consumption = reading.value - source_previous.value if source_previous else None

    if request.method == 'POST' and form.is_valid():
        destination = form.cleaned_data['destination_meter']
        try:
            previous, following = validate_reassignment(reading, destination)
        except ValidationError as error:
            form.add_error('destination_meter', '; '.join(error.messages))
        else:
            preview = {
                'destination': destination,
                'previous': previous,
                'following': following,
                'previous_consumption': reading.value - previous.value if previous else None,
                'following_consumption': following.value - reading.value if following else None,
            }
            if request.POST.get('confirm') == 'yes':
                try:
                    replacement, old_id, linked_count, old_label, new_label, full_reason = reassign_reading(
                        reading_id=reading.pk,
                        destination_meter_id=destination.pk,
                        reason=form.cleaned_data['reason'],
                        actor=request.user,
                        expected_version=form.cleaned_data['reading_version'],
                    )
                except ValidationError as error:
                    form.add_error(None, '; '.join(error.messages))
                else:
                    LogEntry.objects.create(
                        user=request.user,
                        content_type=ContentType.objects.get_for_model(Reading),
                        object_id=str(replacement.pk),
                        object_repr=str(replacement),
                        action_flag=2,
                        change_message=(
                            f'Исправлена привязка показания: Reading #{old_id}; '
                            f'{old_label} → {new_label}; связанных заявок контролёра: {linked_count}; '
                            f'{full_reason}'
                        ),
                    )
                    messages.success(
                        request,
                        f'Привязка исправлена. Новая запись #{replacement.pk}; '
                        f'связанных заявок контролёра перенесено: {linked_count}.',
                    )
                    return HttpResponseRedirect(reverse('admin:water_reading_change', args=[replacement.pk]))

    context = {
        **admin.site.each_context(request),
        'title': f'Исправление привязки показания #{reading.pk}',
        'opts': Reading._meta,
        'reading': reading,
        'form': form,
        'preview': preview,
        'source_previous': source_previous,
        'source_consumption': source_consumption,
        'linked_submissions': reading.controller_submissions.select_related('submitted_by').all(),
    }
    return TemplateResponse(request, 'admin/water/reading/reassign.html', context)
