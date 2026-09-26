from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from .controller_scope import ControllerLineAccess
from .models import ControllerReadingSubmission, Membership, Meter, Reading


ZERO = Decimal('0')


class ControllerWorkspaceDateForm(forms.Form):
    date = forms.DateField(
        label='Дата снятия',
        widget=forms.DateInput(attrs={'type': 'date'}),
        help_text='Обычно показания снимаются в конце месяца.',
    )

    def clean_date(self):
        value = self.cleaned_data['date']
        if value > timezone.localdate():
            raise forms.ValidationError('Дата не может быть в будущем.')
        return value


def _active_accesses(user, on_date):
    return ControllerLineAccess.objects.filter(
        user=user,
        starts__lte=on_date,
    ).filter(
        Q(ends__isnull=True) | Q(ends__gt=on_date),
    ).select_related('group', 'group__node').order_by('group__name', 'group_id')


def _active_meter_queryset(queryset, on_date):
    return queryset.filter(
        Q(commissioned_on__isnull=True) | Q(commissioned_on__lte=on_date),
    ).filter(
        Q(retired_on__isnull=True) | Q(retired_on__gte=on_date),
    )


def _group_meters(group, on_date):
    account_ids = Membership.objects.filter(
        group=group,
        starts__lte=on_date,
        account__archived=False,
    ).filter(
        Q(ends__isnull=True) | Q(ends__gt=on_date),
    ).values_list('account_id', flat=True)

    meters = Meter.objects.select_related('account', 'group', 'node').filter(
        Q(kind='line', group=group)
        | Q(kind='individual', account_id__in=account_ids),
    )
    meters = list(_active_meter_queryset(meters, on_date).order_by(
        'account__plot', 'account__number', 'serial', 'id',
    ))
    return sorted(
        meters,
        key=lambda meter: (
            0 if meter.kind == 'line' else 1,
            (meter.account.plot if meter.account else '') or '',
            (meter.account.number if meter.account else '') or '',
            meter.serial,
            meter.pk,
        ),
    )


def _meter_state(meter, on_date, user, *, posted_value=None, error=''):
    previous = Reading.objects.filter(
        meter=meter, date__lt=on_date,
    ).order_by('-date', '-id').first()
    approved = Reading.objects.filter(
        meter=meter, date=on_date,
    ).order_by('-id').first()
    pending = ControllerReadingSubmission.objects.filter(
        meter=meter,
        date=on_date,
        status='pending',
        submitted_by=user,
        source=ControllerReadingSubmission.SOURCE_LINE_SENIOR,
    ).order_by('-submitted_at', '-id').first()

    if pending is not None:
        effective_value = pending.value
        input_value = str(pending.value)
    elif approved is not None:
        effective_value = approved.value
        input_value = ''
    else:
        effective_value = None
        input_value = ''

    if posted_value is not None:
        input_value = posted_value

    consumption = None
    if effective_value is not None and previous is not None:
        consumption = effective_value - previous.value

    if meter.kind == 'line':
        label = f'Контрольный счётчик линии · {meter.serial}'
    else:
        account = meter.account
        address = account.plot or account.number or f'ID {account.pk}'
        label = f'{address} · {meter.serial}'

    return {
        'meter': meter,
        'label': label,
        'is_line': meter.kind == 'line',
        'previous': previous,
        'approved': approved,
        'pending': pending,
        'input_value': input_value,
        'effective_value': effective_value,
        'consumption': consumption,
        'error': error,
    }


def _summary(rows):
    line_rows = [row for row in rows if row['is_line']]
    individual_rows = [row for row in rows if not row['is_line']]

    missing = [
        row for row in rows
        if row['effective_value'] is None or row['previous'] is None
    ]
    line_complete = bool(line_rows) and all(row['consumption'] is not None for row in line_rows)
    individual_complete = bool(individual_rows) and all(
        row['consumption'] is not None for row in individual_rows
    )

    line_volume = (
        sum((row['consumption'] for row in line_rows), ZERO)
        if line_complete else None
    )
    individual_volume = (
        sum((row['consumption'] for row in individual_rows), ZERO)
        if individual_complete else None
    )
    difference = None
    difference_percent = None
    if line_volume is not None and individual_volume is not None:
        difference = line_volume - individual_volume
        if line_volume > ZERO:
            difference_percent = (difference * Decimal('100') / line_volume).quantize(Decimal('0.01'))
        elif difference == ZERO:
            difference_percent = ZERO

    return {
        'line_volume': line_volume,
        'individual_volume': individual_volume,
        'difference': difference,
        'difference_percent': difference_percent,
        'complete': difference is not None,
        'missing_count': len(missing),
        'individual_count': len(individual_rows),
        'line_meter_count': len(line_rows),
    }


def _build_groups(user, on_date, posted_values=None, errors=None):
    posted_values = posted_values or {}
    errors = errors or {}
    result = []
    for access in _active_accesses(user, on_date):
        meters = _group_meters(access.group, on_date)
        rows = [
            _meter_state(
                meter,
                on_date,
                user,
                posted_value=posted_values.get(meter.pk),
                error=errors.get(meter.pk, ''),
            )
            for meter in meters
        ]
        result.append({
            'access': access,
            'group': access.group,
            'rows': rows,
            'summary': _summary(rows),
        })
    return result


def controller_workspace(request):
    if not request.user.has_perm('water.use_controller_workspace'):
        raise PermissionDenied

    source = request.POST if request.method == 'POST' else request.GET
    date_form = ControllerWorkspaceDateForm(
        source or None,
        initial={'date': timezone.localdate()},
    )
    selected_date = timezone.localdate()
    if date_form.is_valid():
        selected_date = date_form.cleaned_data['date']

    posted_values = {}
    errors = {}

    if request.method == 'POST' and request.POST.get('review_submission'):
        submission_id = request.POST.get('review_submission')
        decision = request.POST.get('decision')
        if decision not in {'confirm', 'flag'}:
            raise PermissionDenied
        with transaction.atomic():
            submission = ControllerReadingSubmission.objects.select_for_update().get(
                pk=submission_id,
                source=ControllerReadingSubmission.SOURCE_RESIDENT,
                status='pending',
                line_review_status=ControllerReadingSubmission.LINE_REVIEW_PENDING,
            )
            allowed_account_ids = Membership.objects.filter(
                group_id__in=_active_accesses(request.user, submission.date).values('group_id'),
                starts__lte=submission.date,
            ).filter(Q(ends__isnull=True) | Q(ends__gt=submission.date)).values_list('account_id', flat=True)
            if submission.meter.kind != 'individual' or submission.meter.account_id not in allowed_account_ids:
                raise PermissionDenied
            submission.line_review_status = (
                ControllerReadingSubmission.LINE_REVIEW_CONFIRMED
                if decision == 'confirm' else ControllerReadingSubmission.LINE_REVIEW_FLAGGED
            )
            submission.line_review_comment = request.POST.get('line_review_comment', '').strip()[:500]
            submission.line_reviewed_by = request.user
            submission.line_reviewed_at = timezone.now()
            submission._history_user = request.user
            submission._change_reason = 'Проверка наблюдения жителя старшим линии'
            submission.save()
        messages.success(request, 'Наблюдение жителя передано администратору с решением старшего линии.')
        return HttpResponseRedirect(f'{reverse("water_controller_workspace")}?date={submission.date.isoformat()}')

    if request.method == 'POST' and date_form.is_valid():
        value_field = forms.DecimalField(
            max_digits=14,
            decimal_places=3,
            min_value=ZERO,
        )
        accesses = list(_active_accesses(request.user, selected_date))
        group_meters = {
            access.group_id: _group_meters(access.group, selected_date)
            for access in accesses
        }
        meters = [meter for access in accesses for meter in group_meters[access.group_id]]

        candidates = []
        for meter in meters:
            raw = request.POST.get(f'value_{meter.pk}', '').strip()
            posted_values[meter.pk] = raw
            if not raw:
                continue
            try:
                value = value_field.clean(raw.replace(',', '.'))
            except ValidationError as error:
                errors[meter.pk] = '; '.join(error.messages)
                continue

            pending = ControllerReadingSubmission.objects.filter(
                meter=meter,
                date=selected_date,
                status='pending',
                submitted_by=request.user,
                source=ControllerReadingSubmission.SOURCE_LINE_SENIOR,
            ).order_by('-submitted_at', '-id').first()
            candidates.append((meter, value, pending))

        if not errors and candidates:
            changed = 0
            try:
                with transaction.atomic():
                    fresh_accesses = list(_active_accesses(request.user, selected_date))
                    still_allowed = {
                        meter.pk
                        for access in fresh_accesses
                        for meter in _group_meters(access.group, selected_date)
                    }
                    if not {meter.pk for meter, _, _ in candidates}.issubset(still_allowed):
                        raise ValidationError('Состав доступных линий изменился. Обновите страницу.')

                    for meter, value, pending in candidates:
                        Meter.objects.select_for_update().get(pk=meter.pk)
                        pending = ControllerReadingSubmission.objects.select_for_update().filter(
                            meter=meter,
                            date=selected_date,
                            status='pending',
                            submitted_by=request.user,
                            source=ControllerReadingSubmission.SOURCE_LINE_SENIOR,
                        ).order_by('-submitted_at', '-id').first()
                        if pending is None:
                            submission = ControllerReadingSubmission(
                                meter=meter,
                                date=selected_date,
                                value=value,
                                submitted_by=request.user,
                                source=ControllerReadingSubmission.SOURCE_LINE_SENIOR,
                            )
                            reason = 'Пакетная подача старшим линии'
                        else:
                            submission = ControllerReadingSubmission.objects.select_for_update().get(pk=pending.pk)
                            if submission.value == value:
                                continue
                            submission.value = value
                            submission.submitted_at = timezone.now()
                            reason = 'Уточнение пакетной подачи старшим линии'
                        submission._history_user = request.user
                        submission._change_reason = reason
                        submission.save()
                        changed += 1
            except (ValidationError, IntegrityError) as error:
                messages.error(
                    request,
                    f'Сохранение отменено: {"; ".join(error.messages) if isinstance(error, ValidationError) else "данные изменились"}.',
                )
            else:
                if changed:
                    messages.success(
                        request,
                        f'Отправлено на проверку показаний: {changed}. Предварительный баланс обновлён ниже.',
                    )
                else:
                    messages.info(request, 'Новых значений для отправки нет.')
                return HttpResponseRedirect(
                    f'{reverse("water_controller_workspace")}?date={selected_date.isoformat()}'
                )
        elif not errors:
            messages.warning(request, 'Введите хотя бы одно новое показание.')

    groups = _build_groups(
        request.user,
        selected_date,
        posted_values=posted_values if errors else None,
        errors=errors,
    )
    context = {
        **admin.site.each_context(request),
        'title': 'Показания моей линии',
        'date_form': date_form,
        'selected_date': selected_date,
        'groups': groups,
        'has_groups': bool(groups),
        'resident_submissions': ControllerReadingSubmission.objects.filter(
            source=ControllerReadingSubmission.SOURCE_RESIDENT,
            status='pending',
            line_review_status=ControllerReadingSubmission.LINE_REVIEW_PENDING,
            date=selected_date,
            meter__kind='individual',
            meter__account_id__in=Membership.objects.filter(
                group_id__in=_active_accesses(request.user, selected_date).values('group_id'),
                starts__lte=selected_date,
            ).filter(Q(ends__isnull=True) | Q(ends__gt=selected_date)).values('account_id'),
        ).select_related('meter__account', 'submitted_by').order_by('meter__account__number', 'id'),
    }
    return TemplateResponse(
        request,
        'admin/water/controller_workspace.html',
        context,
    )
