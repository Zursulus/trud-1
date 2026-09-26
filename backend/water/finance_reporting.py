from decimal import Decimal

from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .finance_models import ChargeObligation
from .models import Charge


MONEY = DecimalField(max_digits=14, decimal_places=2)


def obligation_report_queryset():
    """Return obligation rows with confirmed allocated payments, without inferring people/plots.

    Payment settlement remains account/Charge based. The explicit payer dimensions
    come only from ChargeObligation; ownership or membership is never guessed.
    """
    return (
        ChargeObligation.objects
        .select_related(
            'charge__account',
            'charge__period',
            'plot',
            'person',
            'membership__person',
        )
        .annotate(
            confirmed_paid=Coalesce(
                Sum(
                    'charge__allocations__amount',
                    filter=Q(charge__allocations__payment__status='confirmed'),
                ),
                Value(Decimal('0.00')),
                output_field=MONEY,
            )
        )
    )


def outstanding_amount(obligation):
    paid = getattr(obligation, 'confirmed_paid', None)
    if paid is None:
        paid = obligation.charge.allocations.filter(
            payment__status='confirmed',
        ).aggregate(
            total=Coalesce(Sum('amount'), Value(Decimal('0.00')), output_field=MONEY)
        )['total']
    return (obligation.charge.amount - paid).quantize(Decimal('0.01'))


def explicit_person_id(obligation):
    """Return a person only when the obligation explicitly names one/member record."""
    if obligation.payer_scope == ChargeObligation.PAYER_PERSON:
        return obligation.person_id
    if obligation.payer_scope == ChargeObligation.PAYER_MEMBERSHIP and obligation.membership_id:
        return obligation.membership.person_id
    return None


def explicit_plot_id(obligation):
    """Never derive a plot from Account; return only an explicitly selected plot."""
    return obligation.plot_id


def account_charge_rows(account, *, on_date=None):
    """Build resident-safe rows for one account using allocation-level payment state.

    Person names are deliberately never returned. A finance-capable resident may
    see why an account was charged, but CAP_FINANCE alone must not become a PII
    disclosure channel for another person or member.
    """
    on_date = on_date or timezone.localdate()
    charges = (
        Charge.objects.filter(account=account, status='approved')
        .select_related('period', 'obligation', 'obligation__plot')
        .annotate(
            confirmed_paid=Coalesce(
                Sum(
                    'allocations__amount',
                    filter=Q(allocations__payment__status='confirmed'),
                ),
                Value(Decimal('0.00')),
                output_field=MONEY,
            )
        )
        .order_by('-period__starts', '-id')
    )

    rows = []
    for charge in charges:
        try:
            obligation = charge.obligation
        except ChargeObligation.DoesNotExist:
            obligation = None

        paid = charge.confirmed_paid.quantize(Decimal('0.01'))
        outstanding = (charge.amount - paid).quantize(Decimal('0.01'))
        due_on = obligation.due_on if obligation else None

        if charge.amount < 0:
            state = 'adjustment'
        elif outstanding <= 0:
            state = 'paid'
        elif due_on and due_on < on_date:
            state = 'overdue'
        elif paid > 0:
            state = 'partial'
        else:
            state = 'due'

        payer_hint = ''
        if obligation:
            plot_suffix = f' · участок {obligation.plot.label}' if obligation.plot_id else ''
            if obligation.payer_scope == ChargeObligation.PAYER_PLOT and obligation.plot_id:
                payer_hint = f'Участок {obligation.plot.label}'
            elif obligation.payer_scope == ChargeObligation.PAYER_PERSON:
                payer_hint = f'Персональное обязательство{plot_suffix}'
            elif obligation.payer_scope == ChargeObligation.PAYER_MEMBERSHIP:
                payer_hint = f'Обязательство члена ТСН{plot_suffix}'
            else:
                payer_hint = 'Лицевой счёт'

        rows.append({
            'charge_id': charge.pk,
            'title': obligation.get_category_display() if obligation else charge.get_kind_display(),
            'period_starts': charge.period.starts,
            'amount': charge.amount,
            'paid': paid,
            'outstanding': outstanding,
            'state': state,
            'due_on': due_on,
            'base_amount': obligation.base_amount if obligation else None,
            'relief_amount': obligation.relief_amount if obligation else Decimal('0.00'),
            'payer_hint': payer_hint,
        })
    return rows