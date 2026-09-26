from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from .models import Charge, LandPlot, Person, RecordedModel
from .resident_models import TsnMembership


class ChargeObligation(RecordedModel):
    """Explicit business subject for a charge without changing settlement semantics.

    Charge/Payment/PaymentAllocation remain account-based. This model records why
    an annual or other charge belongs to that account and who/what is the payer
    subject. No ownership, membership or identity is inferred from contacts.
    """

    CATEGORY_MEMBERSHIP = 'membership'
    CATEGORY_TARGET = 'target'
    CATEGORY_WATER = 'water'
    CATEGORY_OTHER = 'other'
    CATEGORY_CHOICES = [
        (CATEGORY_MEMBERSHIP, 'Членский взнос'),
        (CATEGORY_TARGET, 'Целевой взнос'),
        (CATEGORY_WATER, 'Вода'),
        (CATEGORY_OTHER, 'Прочее'),
    ]

    PAYER_ACCOUNT = 'account'
    PAYER_PLOT = 'plot'
    PAYER_PERSON = 'person'
    PAYER_MEMBERSHIP = 'membership'
    PAYER_CHOICES = [
        (PAYER_ACCOUNT, 'Лицевой счёт'),
        (PAYER_PLOT, 'Участок'),
        (PAYER_PERSON, 'Конкретное лицо'),
        (PAYER_MEMBERSHIP, 'Член ТСН'),
    ]

    charge = models.OneToOneField(
        Charge,
        verbose_name='Начисление',
        on_delete=models.PROTECT,
        related_name='obligation',
    )
    category = models.CharField('Категория обязательства', max_length=20, choices=CATEGORY_CHOICES)
    payer_scope = models.CharField('Кто обязан платить', max_length=20, choices=PAYER_CHOICES)
    plot = models.ForeignKey(
        LandPlot,
        verbose_name='Участок обязательства',
        on_delete=models.PROTECT,
        related_name='charge_obligations',
        blank=True,
        null=True,
    )
    person = models.ForeignKey(
        Person,
        verbose_name='Лицо-плательщик',
        on_delete=models.PROTECT,
        related_name='charge_obligations',
        blank=True,
        null=True,
    )
    membership = models.ForeignKey(
        TsnMembership,
        verbose_name='Членство-плательщик',
        on_delete=models.PROTECT,
        related_name='charge_obligations',
        blank=True,
        null=True,
    )
    due_on = models.DateField('Срок оплаты')
    base_amount = models.DecimalField(
        'Сумма до льготы, ₽',
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    relief_amount = models.DecimalField(
        'Льгота / уменьшение, ₽',
        max_digits=14,
        decimal_places=2,
        default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    basis = models.CharField('Основание начисления', max_length=300)
    relief_basis = models.CharField('Основание льготы', max_length=300, blank=True)
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Основание финансового обязательства'
        verbose_name_plural = 'Основания финансовых обязательств'
        ordering = ['charge_id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(relief_amount__lte=models.F('base_amount')),
                name='charge_obligation_relief_lte_base',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        payer_scope='account',
                        plot__isnull=True,
                        person__isnull=True,
                        membership__isnull=True,
                    )
                    | models.Q(
                        payer_scope='plot',
                        plot__isnull=False,
                        person__isnull=True,
                        membership__isnull=True,
                    )
                    | models.Q(
                        payer_scope='person',
                        person__isnull=False,
                        membership__isnull=True,
                    )
                    | models.Q(
                        payer_scope='membership',
                        person__isnull=True,
                        membership__isnull=False,
                    )
                ),
                name='charge_obligation_payer_fields',
            ),
        ]

    def clean(self):
        errors = {}

        rules = {
            self.PAYER_ACCOUNT: (set(), set()),
            self.PAYER_PLOT: ({'plot'}, {'plot'}),
            self.PAYER_PERSON: ({'person'}, {'person', 'plot'}),
            self.PAYER_MEMBERSHIP: ({'membership'}, {'membership', 'plot'}),
        }
        rule = rules.get(self.payer_scope)
        if rule is not None:
            required, allowed = rule
            present = {
                field for field in ('plot', 'person', 'membership')
                if bool(getattr(self, f'{field}_id'))
            }
            if not required.issubset(present) or not present.issubset(allowed):
                errors['payer_scope'] = 'Для выбранного плательщика заполнена неверная комбинация связей.'

        if self.plot_id and self.charge_id:
            if not self.plot.account_id:
                errors['plot'] = 'Участок должен быть явно связан с лицевым счётом до начисления.'
            elif self.plot.account_id != self.charge.account_id:
                errors['plot'] = 'Участок относится к другому лицевому счёту.'

        if self.base_amount is not None and self.relief_amount is not None:
            if self.relief_amount > self.base_amount:
                errors['relief_amount'] = 'Льгота не может превышать базовую сумму.'
            if self.charge_id:
                expected = (self.base_amount - self.relief_amount).quantize(Decimal('0.01'))
                if self.charge.amount != expected:
                    errors['base_amount'] = (
                        'Итог начисления должен равняться базовой сумме за вычетом льготы.'
                    )
                if self.charge.amount < 0:
                    errors['charge'] = 'Отрицательные корректировки оформляются отдельным Charge без обязательства.'

        if self.relief_amount and not self.relief_basis.strip():
            errors['relief_basis'] = 'Для ненулевой льготы укажите основание.'

        if errors:
            raise ValidationError(errors)

    @property
    def financial_period(self):
        return self.charge.period

    def __str__(self):
        return f'{self.charge} · {self.get_category_display()} · {self.get_payer_scope_display()}'