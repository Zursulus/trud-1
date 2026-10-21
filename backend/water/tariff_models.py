"""Immutable, audited tariff previews; approval adds ledger corrections."""
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .models import BillingPolicy, RecordedModel, private_document_path


class TariffRecalculation(RecordedModel):
    period = models.ForeignKey('water.BillingPeriod', on_delete=models.PROTECT, verbose_name='Расчётный период')
    rate = models.DecimalField('Ставка водоканала, ₽/м³', max_digits=10, decimal_places=2,
                               validators=[MinValueValidator(Decimal('0.01'))])
    starts = models.DateField('Тариф действует с')
    ends = models.DateField('Действует до (не включая)', null=True, blank=True)
    basis = models.CharField('Документ-основание (счёт №…)', max_length=300)
    document = models.FileField('Файл основания', upload_to=private_document_path, max_length=300, blank=True)
    loss_distribution = models.CharField('Методика', max_length=30, default='node_excel',
                                         choices=BillingPolicy._meta.get_field('loss_distribution').choices)
    status = models.CharField('Состояние', max_length=12, default='draft', choices=[
        ('draft', 'Черновик'), ('approved', 'Утверждён'), ('cancelled', 'Отменён'),
    ], editable=False)
    result = models.JSONField('Снимок сравнения', editable=False)
    source_digest = models.CharField(max_length=64, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+', editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+',
                                   null=True, blank=True, editable=False)
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = 'Пересчёт тарифа водоканала'
        verbose_name_plural = 'Тариф водоканала → пересчёт периода'
        ordering = ['-created_at', '-pk']
        default_permissions = ('view', 'add')
        permissions = [('approve_tariffrecalculation', 'Может утвердить пересчёт тарифа водоканала')]
        constraints = [models.CheckConstraint(
            condition=models.Q(ends__isnull=True) | models.Q(ends__gt=models.F('starts')),
            name='tariff_recalculation_dates',
        )]

    def clean(self):
        if not self.basis.strip():
            raise ValidationError({'basis': 'Укажите документ-основание.'})
        if self.period_id and self.starts:
            if self.starts > self.period.starts or (self.ends and self.ends < self.period.ends):
                raise ValidationError('Тариф должен покрывать весь расчётный период.')
        if self.loss_distribution == 'manual':
            raise ValidationError('Ручное распределение не рассчитывается автоматически.')
        if self.document and not self.document._committed:
            from .appeal_security import validate_appeal_attachment
            validate_appeal_attachment(self.document.file)
        if self.pk:
            previous = type(self).objects.get(pk=self.pk)
            immutable = ('period_id', 'rate', 'starts', 'ends', 'basis', 'document',
                         'loss_distribution', 'result', 'source_digest', 'created_by_id', 'created_at')
            if any(getattr(previous, field) != getattr(self, field) for field in immutable):
                raise ValidationError('Снимок пересчёта неизменяем. Создайте новый черновик.')
            if previous.status != 'draft':
                raise ValidationError('Решение по пересчёту уже принято.')
        elif self.status != 'draft':
            raise ValidationError('Сначала создайте черновик.')
        if self.status != 'draft' and (not self.decided_by_id or not self.decided_at):
            raise ValidationError('Для решения нужны автор и дата.')

    def __str__(self):
        return f'Пересчёт №{self.pk} · {self.period} · {self.rate} ₽/м³'


def validate_adjustment(charge):
    """The only additional ledger operation allowed in an approved period.

    No flags or validation bypass: every correction must match the approved snapshot.
    """
    recalc = charge.tariff_recalculation
    row = next((row for row in recalc.result['accounts'] if row['account_id'] == charge.account_id), None)
    if (recalc.status != 'approved' or recalc.period_id != charge.period_id
            or (not charge.pk and charge.period.status == 'closed')
            or not row or row['reason'] or row['posting_delta'] is None
            or charge.kind != 'adjustment' or charge.status != 'approved'
            or charge.origin != 'calculation' or charge.volume is not None or charge.rate is not None
            or charge.amount != Decimal(row['posting_delta'])
            or charge.source_key != f'tariff-recalc:{recalc.pk}:{charge.account_id}'):
        raise ValidationError('Корректировка должна точно соответствовать утверждённому пересчёту.')
