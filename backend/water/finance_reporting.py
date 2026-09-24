from decimal import Decimal

from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce

from .finance_models import ChargeObligation


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
    if obligation.payer_scope == ChargeObligation.PAYER_PLOT:
        return obligation.plot_id
    return None
