from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .models import (
    Account, BillingAssignment, BillingPeriod, BillingPolicy, Charge,
    Membership, Meter, Reading, Tariff,
)


@dataclass(frozen=True)
class BillingResult:
    account: Account
    outcome: str
    message: str
    charge: Charge | None = None


def active_group(account, on_date):
    membership = Membership.objects.filter(account=account, starts__lte=on_date).filter(
        Q(ends__isnull=True) | Q(ends__gt=on_date),
    ).select_related('group').order_by('-starts', '-id').first()
    return membership.group if membership else None


def resolve_policy(account, period):
    active = Q(starts__lte=period.starts) & (Q(ends__isnull=True) | Q(ends__gte=period.ends))
    assignment = BillingAssignment.objects.filter(active, account=account).select_related('policy').order_by('-priority', '-starts', '-id').first()
    if assignment:
        return assignment.policy, f'индивидуальные правила «{assignment.policy.name}»'
    group = active_group(account, period.starts)
    if group:
        assignment = BillingAssignment.objects.filter(active, group=group).select_related('policy').order_by('-priority', '-starts', '-id').first()
        if assignment:
            return assignment.policy, f'правила группы «{assignment.policy.name}»'
    policy = BillingPolicy.objects.filter(is_default=True).first()
    return (policy, f'общие правила «{policy.name}»') if policy else (None, 'нет правил по умолчанию')


def resolve_tariff(account, period):
    covers = Q(starts__lte=period.starts) & (Q(ends__isnull=True) | Q(ends__gte=period.ends))
    tariff = Tariff.objects.filter(covers, account=account).order_by('-starts', '-id').first()
    if tariff:
        return tariff, f'индивидуальный тариф «{tariff.name}»'
    group = active_group(account, period.starts)
    if group:
        tariff = Tariff.objects.filter(covers, group=group, account__isnull=True).order_by('-starts', '-id').first()
        if tariff:
            return tariff, f'тариф группы «{tariff.name}»'
    tariff = Tariff.objects.filter(covers, account__isnull=True, group__isnull=True).order_by('-starts', '-id').first()
    return (tariff, f'общий тариф «{tariff.name}»') if tariff else (None, 'нет тарифа на весь период')


def actual_consumption(account, period):
    meters = Meter.objects.filter(
        account=account, kind='individual',
    ).filter(Q(commissioned_on__isnull=True) | Q(commissioned_on__lte=period.starts)).filter(
        Q(retired_on__isnull=True) | Q(retired_on__gte=period.ends),
    )
    if not meters.exists():
        return None, 'нет активного индивидуального счётчика'
    total = Decimal('0')
    details = []
    for meter in meters:
        # Never silently stretch a billing period to convenient nearby readings.
        # Missing boundary readings are handled by the explicitly selected policy.
        first = Reading.objects.filter(meter=meter, date=period.starts).order_by('-id').first()
        last = Reading.objects.filter(meter=meter, date=period.ends).order_by('-id').first()
        if not first or not last:
            return None, f'для счётчика {meter.serial} нет граничных показаний'
        total += last.value - first.value
        details.append(f'{meter.serial}: {last.value}−{first.value}')
    return total, '; '.join(details)


def historical_consumption(account, before_date, periods):
    intervals = []
    for meter in Meter.objects.filter(account=account, kind='individual'):
        values = list(Reading.objects.filter(
            meter=meter, date__lte=before_date,
        ).order_by('-date', '-id').values_list('value', flat=True)[:periods + 1])
        intervals.extend(values[index] - values[index + 1] for index in range(len(values) - 1))
    return intervals[:periods]


def fallback_consumption(account, period, policy):
    method = policy.missing_reading
    if method == 'zero':
        return Decimal('0'), 'нет показаний: нулевой расход по правилу'
    if method == 'norm':
        return policy.monthly_norm_m3, f'нет показаний: норматив {policy.monthly_norm_m3} м³'
    history = historical_consumption(account, period.starts, policy.average_periods)
    if method == 'previous' and history:
        return history[0], f'нет показаний: расход прошлого интервала {history[0]} м³'
    if method == 'average' and history:
        value = sum(history, Decimal('0')) / len(history)
        return value.quantize(Decimal('0.001')), f'нет показаний: среднее по {len(history)} интервалам'
    labels = {'draft': 'требуется ручная проверка', 'manual': 'разрешён только ручной ввод'}
    return None, labels.get(method, 'недостаточно истории для выбранного правила')


def rounded_amount(value, mode):
    if mode == 'none':
        return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    quantum = Decimal('1') if mode in ('ruble', 'up_ruble') else Decimal('0.01')
    rounding = ROUND_CEILING if mode.startswith('up_') else ROUND_HALF_UP
    return value.quantize(quantum, rounding=rounding).quantize(Decimal('0.01'))


@transaction.atomic
def calculate_period(period, *, actor=None):
    if period.status in ('approved', 'closed'):
        raise ValidationError('Утверждённый или закрытый период нельзя пересчитывать.')
    results = []
    accounts = Account.objects.filter(archived=False).order_by('id')
    for account in accounts:
        policy, policy_note = resolve_policy(account, period)
        tariff, tariff_note = resolve_tariff(account, period)
        if policy is None or tariff is None:
            results.append(BillingResult(account, 'skipped', f'{policy_note}; {tariff_note}'))
            continue
        volume, volume_note = actual_consumption(account, period)
        kind = 'water'
        if volume is None:
            volume, volume_note = fallback_consumption(account, period, policy)
            kind = 'norm' if policy.missing_reading == 'norm' and volume is not None else 'water'
        if volume is None:
            results.append(BillingResult(account, 'review', f'{volume_note}; {policy_note}'))
            continue
        amount = rounded_amount(volume * tariff.rate, policy.rounding)
        source_key = f'water:{period.pk}:{account.pk}'
        existing = Charge.objects.filter(source_key=source_key).first()
        if existing and existing.status != 'draft':
            results.append(BillingResult(account, 'review', 'Автоматическое начисление уже утверждено или отменено.'))
            continue
        values = {
            'period': period, 'account': account, 'kind': kind, 'volume': volume,
            'rate': tariff.rate, 'amount': amount, 'status': 'draft',
            'origin': 'calculation',
            'calculation': f'{volume_note}; {tariff_note}; {policy_note}; округление: {policy.get_rounding_display()}',
        }
        if existing:
            for field, value in values.items():
                setattr(existing, field, value)
            existing._history_user = actor
            existing._change_reason = 'Повторный автоматический расчёт'
            existing.save()
            charge = existing
            outcome = 'updated'
        else:
            charge = Charge(source_key=source_key, **values)
            charge._history_user = actor
            charge._change_reason = 'Автоматический расчёт'
            charge.save()
            outcome = 'created'
        results.append(BillingResult(account, outcome, charge.calculation, charge))
    period.status = 'calculated'
    period._history_user = actor
    period._change_reason = 'Расчёт черновиков начислений'
    period.save()
    return results
