from django.contrib import admin

from .admin import RecordedAdmin
from .finance_models import ChargeObligation
from .finance_reporting import obligation_report_queryset, outstanding_amount


@admin.register(ChargeObligation)
class ChargeObligationAdmin(RecordedAdmin):
    """Read-only finance report; PII is shown only with private-registry access."""

    list_filter = ('category', 'payer_scope', 'charge__period')
    ordering = ('-charge__period__starts', 'charge__account_id', 'charge_id')
    actions = None

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        return obligation_report_queryset()

    def get_list_display(self, request):
        payer_column = 'payer_private' if request.user.has_perm('water.access_private_registry') else 'payer_safe'
        return (
            'account_number', 'period_label', 'category', payer_column,
            'due_on', 'base_amount', 'relief_amount', 'final_amount',
            'paid_amount', 'outstanding',
        )

    def get_search_fields(self, request):
        fields = ['charge__account__number', 'charge__account__plot', 'plot__label', 'basis']
        if request.user.has_perm('water.access_private_registry'):
            fields += ['person__full_name', 'membership__person__full_name']
        return fields

    def get_fields(self, request, obj=None):
        common = [
            'charge', 'category', 'payer_scope', 'payer_summary',
            'due_on', 'base_amount', 'relief_amount', 'basis', 'relief_basis', 'notes',
        ]
        if request.user.has_perm('water.access_private_registry'):
            common[4:4] = ['plot', 'person', 'membership']
        else:
            common[4:4] = ['plot']
        return common

    def get_readonly_fields(self, request, obj=None):
        return tuple(self.get_fields(request, obj))

    @admin.display(description='Лицевой счёт', ordering='charge__account__number')
    def account_number(self, obj):
        return obj.charge.account.number or f'ID {obj.charge.account_id}'

    @admin.display(description='Период', ordering='charge__period__starts')
    def period_label(self, obj):
        period = obj.charge.period
        return f'{period.starts:%d.%m.%Y} — {period.ends:%d.%m.%Y}'

    def _plot_suffix(self, obj):
        return f' · участок {obj.plot.label}' if obj.plot_id else ''

    @admin.display(description='Плательщик')
    def payer_safe(self, obj):
        if obj.payer_scope == ChargeObligation.PAYER_PLOT:
            return f'Участок {obj.plot.label}'
        if obj.payer_scope == ChargeObligation.PAYER_PERSON:
            return f'Конкретное лицо (скрыто){self._plot_suffix(obj)}'
        if obj.payer_scope == ChargeObligation.PAYER_MEMBERSHIP:
            return f'Член ТСН (скрыто){self._plot_suffix(obj)}'
        return 'Лицевой счёт'

    @admin.display(description='Плательщик')
    def payer_private(self, obj):
        if obj.payer_scope == ChargeObligation.PAYER_PERSON:
            return f'{obj.person.full_name}{self._plot_suffix(obj)}'
        if obj.payer_scope == ChargeObligation.PAYER_MEMBERSHIP:
            return f'{obj.membership.person.full_name}{self._plot_suffix(obj)}'
        return self.payer_safe(obj)

    @admin.display(description='Плательщик / основание')
    def payer_summary(self, obj):
        # Detail pages must follow the same privacy boundary as the changelist.
        # Django does not pass request into readonly-field methods, so the field
        # itself stays non-PII; explicit Person/Membership fields are included
        # only by get_fields() for users with private-registry permission.
        return self.payer_safe(obj)

    @admin.display(description='Начислено, ₽', ordering='charge__amount')
    def final_amount(self, obj):
        return obj.charge.amount

    @admin.display(description='Оплачено, ₽')
    def paid_amount(self, obj):
        return obj.confirmed_paid

    @admin.display(description='Остаток, ₽')
    def outstanding(self, obj):
        return outstanding_amount(obj)