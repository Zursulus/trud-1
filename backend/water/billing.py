from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from .models import (
    Account, BillingAssignment, BillingPeriod, BillingPolicy, Charge,
    GroupConsumption, Membership, Meter, Payment, PaymentAllocation, Reading,
    Tariff, WaterGroup,
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


def group_volume(group, period, individual_total):
    if group.source == 'individual':
        return individual_total, 'объём группы равен сумме индивидуального расхода'
    if group.source == 'reported':
        reported = GroupConsumption.objects.filter(
            group=group, starts=period.starts, ends=period.ends,
        ).first()
        return (
            (reported.volume, f'передано старшим: {reported.volume} м³')
            if reported else (None, 'нет объёма от старшего за точный период')
        )
    if group.source == 'meter':
        meters = Meter.objects.filter(group=group, kind='line').filter(
            Q(commissioned_on__isnull=True) | Q(commissioned_on__lte=period.starts),
        ).filter(Q(retired_on__isnull=True) | Q(retired_on__gte=period.ends))
        total = Decimal('0')
        found = False
        details = []
        for meter in meters:
            first = Reading.objects.filter(meter=meter, date=period.starts).first()
            last = Reading.objects.filter(meter=meter, date=period.ends).first()
            if not first or not last:
                return None, f'нет граничных показаний счётчика линии {meter.serial}'
            found = True
            total += last.value - first.value
            details.append(f'{meter.serial}: {last.value}−{first.value}')
        return (total, '; '.join(details)) if found else (None, 'нет счётчика линии')
    return None, 'источник группового расхода не определён'


def loss_weights(method, accounts, water_charges):
    if method == 'equal_account':
        return {account.pk: Decimal('1') for account in accounts}
    if method == 'volume':
        return {account.pk: water_charges[account.pk].volume for account in accounts if account.pk in water_charges}
    if method == 'equal_plot':
        return {account.pk: Decimal(account.land_plots.filter(archived=False).count()) for account in accounts}
    if method == 'area':
        result = {}
        for account in accounts:
            areas = account.land_plots.filter(archived=False).values_list('area_m2', flat=True)
            result[account.pk] = sum((area for area in areas if area is not None), Decimal('0'))
        return result
    return {}


def calculate_group_losses(period, *, actor=None):
    results = []
    active = Q(starts__lte=period.starts) & (Q(ends__isnull=True) | Q(ends__gte=period.ends))
    for group in WaterGroup.objects.select_related('node').order_by('id'):
        memberships = Membership.objects.filter(active, group=group).select_related('account').order_by('account_id')
        accounts = [membership.account for membership in memberships if not membership.account.archived]
        if not accounts:
            continue
        assignment = BillingAssignment.objects.filter(active, group=group).select_related('policy').order_by('-priority', '-starts', '-id').first()
        policy = assignment.policy if assignment else BillingPolicy.objects.filter(is_default=True).first()
        if policy is None:
            results.append(BillingResult(accounts[0], 'review', f'{group}: нет правил распределения потерь'))
            continue
        method = policy.loss_distribution
        if method == 'none':
            continue
        if method == 'manual':
            results.append(BillingResult(accounts[0], 'review', f'{group}: потери распределяются только вручную'))
            continue
        water_charges = {
            charge.account_id: charge for charge in Charge.objects.filter(
                period=period, account__in=accounts, source_key__startswith=f'water:{period.pk}:',
                status='draft', volume__isnull=False,
            )
        }
        individual_total = sum((charge.volume for charge in water_charges.values()), Decimal('0'))
        total, source_note = group_volume(group, period, individual_total)
        if total is None:
            results.append(BillingResult(accounts[0], 'review', f'{group}: {source_note}'))
            continue
        loss = total - individual_total
        if loss < 0:
            results.append(BillingResult(
                accounts[0], 'review',
                f'{group}: групповой объём {total} м³ меньше индивидуального {individual_total} м³',
            ))
            continue
        weights = loss_weights(method, accounts, water_charges)
        weight_total = sum(weights.values(), Decimal('0'))
        if weight_total <= 0:
            results.append(BillingResult(accounts[0], 'review', f'{group}: нет данных для способа «{policy.get_loss_distribution_display()}»'))
            continue
        for account in accounts:
            weight = weights.get(account.pk, Decimal('0'))
            share = (loss * weight / weight_total).quantize(Decimal('0.001'))
            tariff, tariff_note = resolve_tariff(account, period)
            if tariff is None:
                results.append(BillingResult(account, 'review', f'{group}: {tariff_note}'))
                continue
            amount = rounded_amount(share * tariff.rate, policy.rounding)
            source_key = f'loss:{period.pk}:{group.pk}:{account.pk}'
            existing = Charge.objects.filter(source_key=source_key).first()
            if existing and existing.status != 'draft':
                results.append(BillingResult(account, 'review', f'{group}: начисление потерь уже утверждено или отменено'))
                continue
            values = {
                'period': period, 'account': account, 'kind': 'loss', 'volume': share,
                'rate': tariff.rate, 'amount': amount, 'status': 'draft', 'origin': 'calculation',
                'calculation': (
                    f'{group}; {source_note}; общий объём {total} м³; индивидуально {individual_total} м³; '
                    f'потери {loss} м³; способ: {policy.get_loss_distribution_display()}; {tariff_note}'
                ),
            }
            if existing:
                for field, value in values.items():
                    setattr(existing, field, value)
                existing._history_user = actor
                existing._change_reason = 'Повторный автоматический расчёт потерь'
                existing.save()
                charge, outcome = existing, 'updated'
            else:
                charge = Charge(source_key=source_key, **values)
                charge._history_user = actor
                charge._change_reason = 'Автоматический расчёт потерь'
                charge.save()
                outcome = 'created'
            results.append(BillingResult(account, outcome, charge.calculation, charge))
    return results


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
    results.extend(calculate_group_losses(period, actor=actor))
    period.status = 'calculated'
    period._history_user = actor
    period._change_reason = 'Расчёт черновиков начислений'
    period.save()
    return results


def policy_on_date(account, on_date):
    active = Q(starts__lte=on_date) & (Q(ends__isnull=True) | Q(ends__gt=on_date))
    assignment = BillingAssignment.objects.filter(active, account=account).select_related('policy').order_by('-priority', '-starts', '-id').first()
    if assignment:
        return assignment.policy
    group = active_group(account, on_date)
    if group:
        assignment = BillingAssignment.objects.filter(active, group=group).select_related('policy').order_by('-priority', '-starts', '-id').first()
        if assignment:
            return assignment.policy
    return BillingPolicy.objects.filter(is_default=True).first()


@transaction.atomic
def allocate_payment(payment, *, actor=None):
    payment = Payment.objects.select_for_update().select_related('account').get(pk=payment.pk)
    if payment.status != 'confirmed':
        raise ValidationError('Распределять можно только подтверждённую оплату.')
    policy = policy_on_date(payment.account, payment.paid_on)
    if policy is None:
        raise ValidationError('Не найден набор правил для распределения оплаты.')
    method = policy.payment_allocation
    if method == 'manual':
        return [], 'По правилам эта оплата распределяется только вручную.'
    allocated = payment.allocations.aggregate(total=Sum('amount'))['total'] or Decimal('0')
    remaining = payment.amount - allocated
    if remaining <= 0:
        return [], 'Оплата уже распределена полностью.'
    charges = list(Charge.objects.filter(
        account=payment.account, status='approved', amount__gt=0,
    ).select_related('period').order_by('period__starts', 'id'))
    candidates = []
    for charge in charges:
        used = charge.allocations.filter(payment__status='confirmed').aggregate(total=Sum('amount'))['total'] or Decimal('0')
        outstanding = charge.amount - used
        if outstanding > 0:
            candidates.append((charge, outstanding))
    if method == 'current':
        candidates.sort(key=lambda item: (
            not (item[0].period.starts <= payment.paid_on < item[0].period.ends),
            item[0].period.starts, item[0].id,
        ))
    elif method == 'reference':
        reference = payment.reference.lower()
        matched = []
        for charge, outstanding in candidates:
            tokens = (
                charge.period.starts.strftime('%m.%Y'),
                charge.period.starts.strftime('%Y-%m'),
            )
            if any(token in reference for token in tokens):
                matched.append((charge, outstanding))
        if not matched:
            return [], 'В назначении платежа не найден однозначный расчётный период.'
        candidates = matched
    created = []
    for charge, outstanding in candidates:
        if remaining <= 0:
            break
        amount = min(remaining, outstanding)
        allocation = PaymentAllocation(payment=payment, charge=charge, amount=amount)
        allocation._history_user = actor
        allocation._change_reason = f'Автоматическое распределение оплаты: {policy.get_payment_allocation_display()}'
        allocation.save()
        created.append(allocation)
        remaining -= amount
    message = f'Распределено {payment.amount - allocated - remaining} ₽; осталось нераспределено {remaining} ₽.'
    return created, message
