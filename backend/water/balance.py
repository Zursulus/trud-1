from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from django import forms
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.template.response import TemplateResponse
from django.utils import timezone

from .models import GroupConsumption, Membership, Meter, Reading, SupplyNode, WaterGroup


ZERO = Decimal('0')


class WaterBalanceForm(forms.Form):
    starts = forms.DateField(
        label='Начало периода',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    ends = forms.DateField(
        label='Конец периода',
        help_text='Нужны показания точно на обе граничные даты.',
        widget=forms.DateInput(attrs={'type': 'date'}),
    )

    def clean(self):
        data = super().clean()
        starts = data.get('starts')
        ends = data.get('ends')
        if starts and ends and ends <= starts:
            self.add_error('ends', 'Конец периода должен быть позже начала.')
        return data


@dataclass
class BalanceLine:
    label: str
    source: str
    volume: Decimal | None
    complete: bool
    detail: str = ''


@dataclass
class NodeBalance:
    node: SupplyNode
    input_lines: list[BalanceLine] = field(default_factory=list)
    group_lines: list[BalanceLine] = field(default_factory=list)
    other_lines: list[BalanceLine] = field(default_factory=list)
    input_known: Decimal = ZERO
    confirmed_known: Decimal = ZERO
    input_volume: Decimal | None = None
    confirmed_volume: Decimal | None = None
    loss_volume: Decimal | None = None
    loss_percent: Decimal | None = None
    complete: bool = False
    issues: list[str] = field(default_factory=list)


@dataclass
class WaterBalanceReport:
    starts: date
    ends: date
    nodes: list[NodeBalance]
    input_known: Decimal
    confirmed_known: Decimal
    input_volume: Decimal | None
    confirmed_volume: Decimal | None
    loss_volume: Decimal | None
    loss_percent: Decimal | None
    complete: bool


def _active_meters(queryset, starts, ends):
    return queryset.filter(
        Q(commissioned_on__isnull=True) | Q(commissioned_on__lte=starts),
    ).filter(
        Q(retired_on__isnull=True) | Q(retired_on__gte=ends),
    ).order_by('serial', 'id')


def _meter_line(meter, starts, ends, *, label=None):
    first = Reading.objects.filter(meter=meter, date=starts).order_by('-id').first()
    last = Reading.objects.filter(meter=meter, date=ends).order_by('-id').first()
    display = label or meter.serial
    if first is None or last is None:
        missing = []
        if first is None:
            missing.append(f'{starts:%d.%m.%Y}')
        if last is None:
            missing.append(f'{ends:%d.%m.%Y}')
        return BalanceLine(
            label=display,
            source=meter.get_kind_display(),
            volume=None,
            complete=False,
            detail='Нет граничного показания: ' + ', '.join(missing),
        )
    volume = last.value - first.value
    return BalanceLine(
        label=display,
        source=meter.get_kind_display(),
        volume=volume,
        complete=True,
        detail=f'{last.value} − {first.value} м³',
    )


def _meter_total(meters, starts, ends, *, empty_message):
    meters = list(meters)
    if not meters:
        return None, [], [empty_message]
    lines = [_meter_line(meter, starts, ends) for meter in meters]
    issues = [f'{line.label}: {line.detail}' for line in lines if not line.complete]
    if issues:
        return None, lines, issues
    return sum((line.volume for line in lines if line.volume is not None), ZERO), lines, []


def _individual_group_line(group, starts, ends):
    memberships = Membership.objects.filter(
        group=group,
        starts__lte=starts,
    ).filter(
        Q(ends__isnull=True) | Q(ends__gte=ends),
        account__archived=False,
    ).select_related('account').order_by('account_id')
    memberships = list(memberships)
    if not memberships:
        return BalanceLine(
            label=group.name,
            source='Сумма индивидуальных счётчиков',
            volume=None,
            complete=False,
            detail='Нет участников группы, покрывающих весь период.',
        )

    total = ZERO
    problems = []
    counted = 0
    for membership in memberships:
        account = membership.account
        meters = _active_meters(
            Meter.objects.filter(account=account, kind='individual'), starts, ends,
        )
        meters = list(meters)
        if not meters:
            problems.append(f'{account}: нет индивидуального счётчика на весь период')
            continue
        for meter in meters:
            line = _meter_line(meter, starts, ends, label=f'{account} · {meter.serial}')
            if not line.complete:
                problems.append(f'{line.label}: {line.detail}')
            else:
                total += line.volume
                counted += 1
    if problems:
        return BalanceLine(
            label=group.name,
            source='Сумма индивидуальных счётчиков',
            volume=None,
            complete=False,
            detail='; '.join(problems),
        )
    return BalanceLine(
        label=group.name,
        source='Сумма индивидуальных счётчиков',
        volume=total,
        complete=True,
        detail=f'Учтено приборов: {counted}.',
    )


def _group_line(group, starts, ends):
    if group.source == 'reported':
        reported = GroupConsumption.objects.filter(
            group=group, starts=starts, ends=ends,
        ).first()
        if reported is None:
            return BalanceLine(
                label=group.name,
                source='Кубы от старшего',
                volume=None,
                complete=False,
                detail='Нет переданного объёма за точный период.',
            )
        return BalanceLine(
            label=group.name,
            source='Кубы от старшего',
            volume=reported.volume,
            complete=True,
            detail=f'Передал: {reported.reported_by}.',
        )

    if group.source == 'meter':
        meters = _active_meters(
            Meter.objects.filter(group=group, kind='line'), starts, ends,
        )
        total, lines, issues = _meter_total(
            meters, starts, ends, empty_message='Нет счётчика линии на весь период.',
        )
        return BalanceLine(
            label=group.name,
            source='Контрольный счётчик линии',
            volume=total,
            complete=not issues,
            detail='; '.join(issues) if issues else '; '.join(f'{line.label}: {line.detail}' for line in lines),
        )

    if group.source == 'individual':
        return _individual_group_line(group, starts, ends)

    return BalanceLine(
        label=group.name,
        source='Источник не определён',
        volume=None,
        complete=False,
        detail='Выберите источник расхода группы: кубы старшего, индивидуальные или счётчик линии.',
    )


def calculate_water_balance(starts, ends):
    nodes = []
    for node in SupplyNode.objects.order_by('name', 'id'):
        result = NodeBalance(node=node)

        main_meters = _active_meters(Meter.objects.filter(node=node, kind='main'), starts, ends)
        input_total, input_lines, input_issues = _meter_total(
            main_meters, starts, ends, empty_message='Нет общего счётчика на весь период.',
        )
        result.input_lines = input_lines
        result.input_known = sum((line.volume for line in input_lines if line.volume is not None), ZERO)
        result.input_volume = input_total
        result.issues.extend(input_issues)

        for group in WaterGroup.objects.filter(node=node).order_by('name', 'id'):
            line = _group_line(group, starts, ends)
            result.group_lines.append(line)
            if line.volume is not None:
                result.confirmed_known += line.volume
            if not line.complete:
                result.issues.append(f'{group.name}: {line.detail}')

        irrigation_meters = list(_active_meters(Meter.objects.filter(node=node, kind='irrigation'), starts, ends))
        for meter in irrigation_meters:
            line = _meter_line(meter, starts, ends)
            line.source = 'Отдельно учтённый полив'
            result.other_lines.append(line)
            if line.volume is not None:
                result.confirmed_known += line.volume
            if not line.complete:
                result.issues.append(f'{meter.serial}: {line.detail}')

        outflow_complete = all(line.complete for line in result.group_lines + result.other_lines)
        result.complete = input_total is not None and outflow_complete
        if result.complete:
            result.confirmed_volume = result.confirmed_known
            result.loss_volume = input_total - result.confirmed_volume
            if input_total > ZERO:
                result.loss_percent = (result.loss_volume * Decimal('100') / input_total).quantize(Decimal('0.01'))
            elif result.loss_volume == ZERO:
                result.loss_percent = ZERO
        nodes.append(result)

    all_complete = bool(nodes) and all(node.complete for node in nodes)
    input_known = sum((node.input_known for node in nodes), ZERO)
    confirmed_known = sum((node.confirmed_known for node in nodes), ZERO)
    input_volume = sum((node.input_volume for node in nodes if node.input_volume is not None), ZERO) if all_complete else None
    confirmed_volume = sum((node.confirmed_volume for node in nodes if node.confirmed_volume is not None), ZERO) if all_complete else None
    loss_volume = input_volume - confirmed_volume if all_complete else None
    loss_percent = None
    if all_complete and input_volume > ZERO:
        loss_percent = (loss_volume * Decimal('100') / input_volume).quantize(Decimal('0.01'))
    elif all_complete and loss_volume == ZERO:
        loss_percent = ZERO

    return WaterBalanceReport(
        starts=starts,
        ends=ends,
        nodes=nodes,
        input_known=input_known,
        confirmed_known=confirmed_known,
        input_volume=input_volume,
        confirmed_volume=confirmed_volume,
        loss_volume=loss_volume,
        loss_percent=loss_percent,
        complete=all_complete,
    )


def water_balance_view(request):
    required = ('water.view_reading', 'water.view_meter', 'water.view_watergroup')
    if not all(request.user.has_perm(permission) for permission in required):
        raise PermissionDenied

    today = timezone.localdate()
    first_of_month = today.replace(day=1)
    source = request.GET or None
    form = WaterBalanceForm(source, initial={'starts': first_of_month, 'ends': today})
    report = None
    if form.is_valid():
        report = calculate_water_balance(form.cleaned_data['starts'], form.cleaned_data['ends'])

    context = {
        **request.admin_site.each_context(request) if hasattr(request, 'admin_site') else {},
        'title': 'Водный баланс и контроль потерь',
        'form': form,
        'report': report,
    }
    return TemplateResponse(request, 'admin/water/balance.html', context)
