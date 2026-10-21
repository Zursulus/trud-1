"""Tariff preview/approval use cases. Decimal calculations, no ledger rewrites."""
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .balance import _active_meters, _meter_total, calculate_water_balance
from .billing import actual_consumption, calculate_period, group_volume, loss_weights, rounded_amount
from .models import (Account, BillingAssignment, BillingPeriod, BillingPolicy, Charge,
                     GroupConsumption, LandPlot, Membership, Meter, Reading, SupplyNode,
                     Tariff, WaterGroup)
from .tariff_models import TariffRecalculation

ZERO = Decimal('0')
CENT = Decimal('0.01')


def money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def require_permission(actor, action='view'):
    if not actor.is_active or not actor.is_staff or not actor.has_perm(f'water.{action}_tariffrecalculation'):
        raise PermissionDenied


def audit(item, actor, message):
    LogEntry.objects.create(user=actor, content_type=ContentType.objects.get_for_model(item),
                            object_id=str(item.pk), object_repr=str(item)[:200],
                            action_flag=CHANGE, change_message=message)


def sources(period, *, lock=False):
    # Version tokens detect changes even when their resulting total stays the same.
    # Parent locks also serialize normal Reading/Membership/Charge.save() paths.
    querysets = [Account.objects.all(), SupplyNode.objects.all(), WaterGroup.objects.all(),
                 Meter.objects.all(), Membership.objects.all(), Reading.objects.all(),
                 GroupConsumption.objects.all(), LandPlot.objects.all(), Charge.objects.filter(period=period),
                 BillingPolicy.objects.all(), BillingAssignment.objects.all(), Tariff.objects.all()]
    tokens = [('period', period.pk, period.version)]
    for queryset in querysets:
        if lock:
            queryset = queryset.select_for_update()
        tokens.append((queryset.model.__name__, list(queryset.order_by('pk').values_list('pk', 'version'))))
    return hashlib.sha256(json.dumps(tokens, ensure_ascii=False).encode()).hexdigest()


def _water_charge(charge):
    return charge.kind in ('water', 'loss', 'norm') or charge.tariff_recalculation_id is not None


def _consumption(account, period, group):
    # A replacement/transfer within a period needs an explicit reconciliation.
    meters = Meter.objects.filter(account=account, kind='individual').filter(
        Q(commissioned_on__isnull=True) | Q(commissioned_on__lt=period.ends),
    ).filter(Q(retired_on__isnull=True) | Q(retired_on__gt=period.starts))
    for meter in meters:
        if meter.node_id != group.node_id:
            return None, 'Счётчик и линия относятся к разным узлам.'
        if ((meter.commissioned_on and meter.commissioned_on > period.starts)
                or (meter.retired_on and meter.retired_on < period.ends)):
            return None, 'Замена счётчика внутри периода: нужна ручная проверка.'
    volume, detail = actual_consumption(account, period)
    if volume is not None and volume < 0:
        return None, 'Отрицательный расход: нужна ручная проверка.'
    return volume, detail


def _node_input(node, period):
    meters = Meter.objects.filter(node=node, kind='main')
    partial = meters.filter(
        Q(commissioned_on__isnull=True) | Q(commissioned_on__lt=period.ends),
    ).filter(Q(retired_on__isnull=True) | Q(retired_on__gt=period.starts)).filter(
        Q(commissioned_on__gt=period.starts) | Q(retired_on__lt=period.ends),
    )
    if partial.exists():
        return None, [], ['Замена общего счётчика ОСН внутри периода: нужна ручная проверка.']
    return _meter_total(_active_meters(meters, period.starts, period.ends), period.starts, period.ends,
                        empty_message='Нет общего счётчика ОСН на весь период.')


def _preview_node_charges(period, nodes, accounts):
    """Use unsaved group tariffs and the ordinary billing engine, without ledger writes."""
    rates = {node['node_id']: node['rate'] for node in nodes}
    tariffs = {
        group.pk: Tariff(name=f'Пересчёт узла {group.node_id}', group=group,
                         starts=period.starts, ends=period.ends, rate=rates[group.node_id])
        for group in WaterGroup.objects.all() if rates[group.node_id] is not None
    }
    by_account = {row['account_id']: row for row in accounts}
    for result in calculate_period(period, preview=True, group_tariffs=tariffs):
        row = by_account.get(result.account.pk)
        if row is None or row['reason']:
            continue
        if result.charge is None:
            row['reason'] = result.message
        else:
            row['rate'] = result.charge.rate
            row['new'] = (row['new'] or ZERO) + result.charge.amount


def build_comparison(item):
    period, rate = item.period, item.rate
    accounts, groups, nodes = [], [], []
    charges = list(Charge.objects.filter(period=period).order_by('pk'))
    old_by_account = {}
    for charge in charges:
        if charge.status == 'approved' and _water_charge(charge):
            old_by_account[charge.account_id] = old_by_account.get(charge.account_id, ZERO) + charge.amount
    eligible = Account.objects.filter(Q(archived=False) | Q(pk__in=old_by_account)).order_by('pk')
    for account in eligible:
        memberships = list(Membership.objects.filter(account=account, starts__lt=period.ends).filter(
            Q(ends__isnull=True) | Q(ends__gt=period.starts),
        ).select_related('group__node'))
        group = memberships[0].group if len(memberships) == 1 else None
        reason, volume, detail = '', None, ''
        if account.archived:
            reason = 'Архивный счёт с начислениями: нужна ручная проверка.'
        elif not group or memberships[0].starts > period.starts or (
                memberships[0].ends and memberships[0].ends < period.ends):
            reason = 'Нет однозначной линии на весь период.'
        else:
            volume, detail = _consumption(account, period, group)
            if volume is None:
                reason = detail
        own_charges = [charge for charge in charges if charge.account_id == account.pk]
        pending = [charge for charge in own_charges if charge.status == 'draft' and _water_charge(charge)]
        approved_old = old_by_account.get(account.pk, ZERO)
        if any(charge.status in ('approved', 'draft') and charge.kind == 'adjustment'
               and not charge.tariff_recalculation_id for charge in own_charges):
            reason = 'Есть ручные корректировки: требуется сверка их назначения.'
        accounts.append(dict(account_id=account.pk, number=account.number or f'ID {account.pk}',
                             plot=account.plot, group_id=group.pk if group else None,
                             group=group.name if group else '', node_id=group.node_id if group else None,
                             node=group.node.name if group else '', volume=volume, detail=detail,
                             old=approved_old + sum((charge.amount for charge in pending), ZERO),
                             approved_old=approved_old, pending_ids=[charge.pk for charge in pending],
                             new=None, rate=None, delta=None, posting_delta=None, percent=None, reason=reason))

    for group in WaterGroup.objects.select_related('node').order_by('pk'):
        rows = [row for row in accounts if row['group_id'] == group.pk]
        confirmed = sum((row['volume'] for row in rows if row['volume'] is not None), ZERO)
        volume, detail = group_volume(group, period, confirmed)
        summary = dict(group_id=group.pk, node_id=group.node_id, name=group.name, node=group.node.name,
                       input=volume, confirmed=confirmed,
                       loss=volume - confirmed if volume is not None else None, detail=detail)
        groups.append(summary)
        if item.loss_distribution == 'node_excel':
            continue
        ready = [row for row in rows if not row['reason']]
        problem = ''
        if item.loss_distribution != 'none':
            if any(row['reason'] for row in rows):
                problem = 'Не все счета линии подтверждены; распределение потерь требует проверки.'
            elif volume is None or volume < confirmed:
                problem = 'Нет достоверного объёма линии или он меньше суммы абонентов.'
        model_accounts = list(Account.objects.filter(pk__in=[row['account_id'] for row in ready]))
        weights = loss_weights(item.loss_distribution, model_accounts, {
            row['account_id']: SimpleNamespace(volume=row['volume']) for row in ready
        })
        weight_total = sum(weights.values(), ZERO)
        if item.loss_distribution != 'none' and weight_total <= 0:
            problem = 'Нет данных для выбранного распределения потерь.'
        for row in ready:
            if problem:
                row['reason'] = problem
                continue
            row['rate'] = rate
            row['new'] = rounded_amount(row['volume'] * rate, 'kopeck')
            if item.loss_distribution != 'none':
                share = ((volume - confirmed) * weights.get(row['account_id'], ZERO) / weight_total).quantize(
                    Decimal('0.001'))  # Same volume rounding as calculate_group_losses().
                row['new'] += rounded_amount(share * rate, 'kopeck')

    balances = ({balance.node.pk: balance for balance in calculate_water_balance(period.starts, period.ends).nodes}
                if item.loss_distribution == 'node_excel' else {})
    for node in SupplyNode.objects.order_by('pk'):
        rows = [row for row in accounts if row['node_id'] == node.pk]
        input_volume, input_lines, issues = _node_input(node, period)
        confirmed = (balances[node.pk].confirmed_known if item.loss_distribution == 'node_excel'
                     else sum((row['volume'] for row in rows if row['volume'] is not None), ZERO))
        node_rate, formula = None, ''
        if item.loss_distribution == 'node_excel':
            if input_volume is None or input_volume < 0 or confirmed <= 0 or input_volume < confirmed:
                reason = '; '.join(issues) or 'Узел требует проверки: вход меньше расхода линий или подтверждённый объём равен нулю.'
                if not issues:
                    issues.append(reason)
                for row in rows:
                    if not row['reason']:
                        row['reason'] = reason
            else:
                node_rate = money(rate * input_volume / confirmed)
                formula = f'{rate:.2f} × {input_volume.normalize():f} / {confirmed.normalize():f} = {node_rate:.2f}'
        nodes.append(dict(node_id=node.pk, name=node.name, input=input_volume, confirmed=confirmed,
                          loss=input_volume - confirmed if input_volume is not None else None,
                          rate=node_rate, formula=formula.replace('.', ','), issues=issues,
                          input_detail='; '.join(f'{line.label}: {line.detail}' for line in input_lines),
                          excluded=sum(bool(row['reason']) for row in rows)))

    if item.loss_distribution == 'node_excel':
        _preview_node_charges(period, nodes, accounts)

    for row in accounts:
        if row['reason']:
            row['new'] = row['delta'] = row['percent'] = row['rate'] = None
        elif row['new'] is None:
            row['reason'] = 'Недостаточно данных для расчёта.'
        else:
            row['delta'] = money(row['new'] - row['old'])
            row['posting_delta'] = money(row['new'] - row['approved_old'])
            row['percent'] = money(row['delta'] * 100 / row['old']) if row['old'] else None
    for summary, key in [(node, 'node_id') for node in nodes] + [(group, 'group_id') for group in groups]:
        rows = [row for row in accounts if row[key] == summary[key]]
        if key == 'node_id':
            summary['excluded'] = sum(bool(row['reason']) for row in rows)
        summary['old'] = sum((row['old'] for row in rows), ZERO)
        summary['new'] = sum((row['new'] if row['new'] is not None else row['old'] for row in rows), ZERO)
        summary['delta'] = summary['new'] - summary['old']
    complete_input = all(node['input'] is not None for node in nodes)
    totals = dict(input=sum((node['input'] for node in nodes), ZERO) if complete_input else None,
                  confirmed=sum((node['confirmed'] for node in nodes), ZERO),
                  old=sum((row['old'] for row in accounts), ZERO),
                  new=sum((row['new'] if row['new'] is not None else row['old'] for row in accounts), ZERO),
                  review=sum(bool(row['reason']) for row in accounts), ready=sum(not row['reason'] for row in accounts))
    totals['loss'] = totals['input'] - totals['confirmed'] if complete_input else None
    totals['delta'] = totals['new'] - totals['old']
    # JSON stores exact decimal strings, never binary floats.
    return json.loads(json.dumps(dict(accounts=accounts, nodes=nodes, groups=groups, totals=totals),
                                 default=lambda value: str(value) if isinstance(value, Decimal) else value))


@transaction.atomic
def create_recalculation(*, actor, period, rate, starts, ends=None, basis, loss_distribution='node_excel', document=None):
    require_permission(actor, 'view')
    require_permission(actor, 'add')
    period = BillingPeriod.objects.select_for_update().get(pk=period.pk)
    if period.status == 'closed':
        raise ValidationError('Закрытый период нельзя пересчитать: корректировка оформляется следующим периодом.')
    digest = sources(period, lock=True)
    item = TariffRecalculation(period=period, rate=rate, starts=starts, ends=ends, basis=basis.strip(),
                               loss_distribution=loss_distribution, document=document or '',
                               created_by=actor, source_digest=digest)
    item.full_clean(exclude=['result'])
    item.result = build_comparison(item)
    if sources(period) != digest:
        raise ValidationError('Исходные данные изменились во время расчёта. Повторите создание черновика.')
    item._history_user = actor
    item._change_reason = 'Черновик пересчёта тарифа водоканала'
    item.save()
    audit(item, actor, f'Создан черновик; основание: {item.basis}')
    return item


@transaction.atomic
def decide_recalculation(*, recalc_id, actor, approve=False):
    require_permission(actor, 'view')
    require_permission(actor, 'approve' if approve else 'add')
    # Same period-first order as Charge.save(); avoids approval/ledger deadlocks.
    period_id = TariffRecalculation.objects.values_list('period_id', flat=True).get(pk=recalc_id)
    period = BillingPeriod.objects.select_for_update().get(pk=period_id)
    item = TariffRecalculation.objects.select_for_update().get(pk=recalc_id)
    if item.status != 'draft':
        raise ValidationError('Решение по пересчёту уже принято.')
    if approve:
        if period.status == 'closed':
            raise ValidationError('Закрытый период нельзя пересчитать.')
        if sources(period, lock=True) != item.source_digest or build_comparison(item) != item.result:
            raise ValidationError('Данные изменились после предпросмотра. Создайте новый черновик.')
        if not item.result['totals']['ready']:
            raise ValidationError('Нет счетов, готовых к утверждению.')
    item.status = 'approved' if approve else 'cancelled'
    item.decided_by, item.decided_at = actor, timezone.now()
    item._history_user = actor
    item._change_reason = 'Утверждение пересчёта' if approve else 'Отмена черновика пересчёта'
    item.save()
    if approve:
        for row in item.result['accounts']:
            if row['reason']:
                continue
            for pending in Charge.objects.filter(pk__in=row['pending_ids']).order_by('pk'):
                pending.status = 'cancelled'
                pending._history_user = actor
                pending._change_reason = f'Заменено пересчётом тарифа №{item.pk}'
                pending.save()
                audit(pending, actor, f'Черновик заменён пересчётом №{item.pk}; основание: {item.basis}')
            if Decimal(row['posting_delta']) == ZERO:
                continue
            charge = Charge(account_id=row['account_id'], period=period, kind='adjustment',
                            amount=Decimal(row['posting_delta']), status='approved', origin='calculation',
                            tariff_recalculation=item, source_key=f'tariff-recalc:{item.pk}:{row["account_id"]}',
                            notes=f'{item.basis}; пересчёт №{item.pk}',
                            calculation=(f'Вода: {row["old"]} → {row["new"]} ₽; '
                                         f'ранее утверждено {row["approved_old"]} ₽; '
                                         f'{item.get_loss_distribution_display()}'))
            charge._history_user = actor
            charge._change_reason = f'Пересчёт тарифа №{item.pk}'
            charge.save()
            audit(charge, actor, f'Корректировка по пересчёту №{item.pk}; основание: {item.basis}')
    audit(item, actor, f'{item.get_status_display()}; основание: {item.basis}')
    return item
