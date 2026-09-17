from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from simple_history.models import HistoricalRecords


class User(AbstractUser):
    pass


class RecordedModel(models.Model):
    """Versioned writes with validation and a history snapshot on every save.

    Application writes must use save(), never QuerySet.update/bulk_update.
    The version also prevents a stale admin form from overwriting a newer edit.
    """
    version = models.PositiveIntegerField(default=0)
    history = HistoricalRecords(inherit=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if not self._state.adding:
                stored = type(self).objects.select_for_update().get(pk=self.pk)
                if stored.version != self.version:
                    raise ValidationError('Запись уже изменена. Обновите страницу и повторите ввод.')
            self.full_clean()
            self.version += 1
            if kwargs.get('update_fields'):
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'version'}
            return super().save(*args, **kwargs)


class Account(RecordedModel):
    seed_slot = models.PositiveIntegerField(null=True, unique=True, editable=False)
    number = models.CharField('Лицевой счёт', max_length=40, blank=True, null=True, unique=True)
    plot = models.CharField('Участок / адрес', max_length=200, blank=True)
    contact_name = models.CharField('ФИО контактного лица', max_length=200, blank=True)
    phone = models.CharField('Телефон', max_length=40, blank=True)
    notes = models.TextField('Примечание', blank=True)
    archived = models.BooleanField('Архивная карточка', default=False)

    class Meta:
        verbose_name = 'Лицевой счёт'
        verbose_name_plural = '01 · Лицевые счета'
        ordering = ['id']
        permissions = [('export_account', 'Может выгружать карточки в CSV')]

    def clean(self):
        self.number = (self.number or '').strip() or None

    def __str__(self):
        return f'{self.number or "ID " + str(self.pk)} · {self.plot or "участок не заполнен"}'


class SupplyNode(RecordedModel):
    name = models.CharField('Общий узел водоснабжения', max_length=200, unique=True)
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Общий узел'
        verbose_name_plural = '02 · Общие узлы'

    def __str__(self):
        return self.name


class WaterGroup(RecordedModel):
    name = models.CharField('Линия / группа', max_length=200, unique=True)
    node = models.ForeignKey(SupplyNode, verbose_name='Общий узел', on_delete=models.PROTECT)
    source = models.CharField('Основной источник расхода', max_length=20, choices=[
        ('unknown', 'Пока не определён'), ('reported', 'Кубы, переданные старшим'),
        ('individual', 'Сумма индивидуальных расходов'), ('meter', 'Счётчик линии'),
    ], default='unknown')
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Линия / группа'
        verbose_name_plural = '03 · Линии и группы'

    def __str__(self):
        return self.name


class Membership(RecordedModel):
    account = models.ForeignKey(Account, verbose_name='Лицевой счёт', on_delete=models.PROTECT)
    group = models.ForeignKey(WaterGroup, verbose_name='Группа', on_delete=models.PROTECT)
    starts = models.DateField('В группе с')
    ends = models.DateField('В группе до (не включая)', blank=True, null=True)

    class Meta:
        verbose_name = 'Участник группы'
        verbose_name_plural = '04 · Состав групп'
        constraints = [models.CheckConstraint(condition=Q(ends__isnull=True) | Q(ends__gt=models.F('starts')), name='membership_dates')]

    def clean(self):
        if self.account_id and self.starts:
            others = Membership.objects.filter(account_id=self.account_id).exclude(pk=self.pk)
            others = others.filter(Q(ends__isnull=True) | Q(ends__gt=self.starts))
            if self.ends:
                others = others.filter(starts__lt=self.ends)
            if others.exists():
                raise ValidationError('У этого счёта уже есть группа на указанные даты.')

    def save(self, *args, **kwargs):
        with transaction.atomic():
            Account.objects.select_for_update().get(pk=self.account_id)
            return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.account} → {self.group}'


class Meter(RecordedModel):
    serial = models.CharField('Номер / обозначение счётчика', max_length=100)
    kind = models.CharField('Назначение', max_length=20, choices=[
        ('main', 'Общий'), ('line', 'Линии / контрольный'),
        ('individual', 'Индивидуальный'), ('irrigation', 'Полив'),
    ])
    node = models.ForeignKey(SupplyNode, verbose_name='Общий узел', on_delete=models.PROTECT)
    account = models.ForeignKey(Account, verbose_name='Лицевой счёт', on_delete=models.PROTECT, blank=True, null=True)
    group = models.ForeignKey(WaterGroup, verbose_name='Линия / группа', on_delete=models.PROTECT, blank=True, null=True)
    retired_on = models.DateField('Снят с учёта', blank=True, null=True)

    class Meta:
        verbose_name = 'Счётчик'
        verbose_name_plural = '05 · Счётчики'
        constraints = [models.UniqueConstraint(fields=['node', 'serial'], name='meter_node_serial')]

    def clean(self):
        if self.pk and self.reading_set.exists():
            original = Meter.objects.get(pk=self.pk)
            if any(getattr(original, key) != getattr(self, key) for key in ('node_id', 'group_id', 'account_id', 'kind')):
                raise ValidationError('Счётчик с показаниями нельзя переносить. Создайте отдельный счётчик.')
            if self.retired_on and self.reading_set.filter(date__gt=self.retired_on).exists():
                raise ValidationError('Есть показания позже даты снятия с учёта.')
        if (self.kind == 'individual') != bool(self.account_id):
            raise ValidationError('Лицевой счёт указывается только для индивидуального счётчика и обязателен для него.')
        if self.kind == 'line' and not self.group_id:
            raise ValidationError('Укажите группу для счётчика линии.')
        if self.kind in ('main', 'irrigation') and self.group_id:
            raise ValidationError('Общий счётчик и полив относятся к узлу, без группы.')
        if self.group_id and self.group.node_id != self.node_id:
            raise ValidationError('Группа и счётчик должны относиться к одному общему узлу.')

    def __str__(self):
        return f'{self.serial} · {self.get_kind_display()}'


class Reading(RecordedModel):
    meter = models.ForeignKey(Meter, verbose_name='Счётчик', on_delete=models.PROTECT)
    date = models.DateField('Дата снятия')
    value = models.DecimalField('Показание счётчика, м³', max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal('0'))])
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Показание счётчика'
        verbose_name_plural = '06 · Показания счётчиков'
        ordering = ['-date', '-id']
        constraints = [
            models.UniqueConstraint(fields=['meter', 'date'], name='reading_meter_date'),
            models.CheckConstraint(condition=Q(value__gte=0), name='reading_nonnegative'),
        ]

    def clean(self):
        if not self.meter_id or not self.date or self.value is None:
            return
        if self.pk:
            original = Reading.objects.get(pk=self.pk)
            if original.meter_id != self.meter_id:
                raise ValidationError('У сохранённого показания нельзя менять счётчик. Исправление привязки требует отдельного разбора.')
        if self.meter.retired_on and self.date > self.meter.retired_on:
            raise ValidationError('Дата позже снятия счётчика с учёта.')
        previous = Reading.objects.filter(meter_id=self.meter_id, date__lt=self.date).exclude(pk=self.pk).order_by('-date').first()
        following = Reading.objects.filter(meter_id=self.meter_id, date__gt=self.date).exclude(pk=self.pk).order_by('date').first()
        if previous and self.value < previous.value or following and self.value > following.value:
            raise ValidationError('Показание нарушает последовательность. Проверьте цифры; при замене заведите новый счётчик.')

    def save(self, *args, **kwargs):
        with transaction.atomic():
            Meter.objects.select_for_update().get(pk=self.meter_id)
            return super().save(*args, **kwargs)

    @property
    def consumption(self):
        previous = Reading.objects.filter(meter=self.meter, date__lt=self.date).order_by('-date').first()
        return self.value - previous.value if previous else None

    def __str__(self):
        return f'{self.meter} · {self.date} · {self.value}'


class GroupConsumption(RecordedModel):
    group = models.ForeignKey(WaterGroup, verbose_name='Группа', on_delete=models.PROTECT)
    starts = models.DateField('Начало периода')
    ends = models.DateField('Конец периода (не включая)')
    volume = models.DecimalField('Расход группы за период, м³', max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal('0'))])
    reported_by = models.CharField('Кто передал / старший', max_length=200)
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Расход группы'
        verbose_name_plural = '07 · Кубы от старших'
        ordering = ['-starts', 'group']
        constraints = [
            models.CheckConstraint(condition=Q(ends__gt=models.F('starts')), name='group_period_dates'),
            models.CheckConstraint(condition=Q(volume__gte=0), name='group_volume_nonnegative'),
            models.UniqueConstraint(fields=['group', 'starts', 'ends'], name='group_period_unique'),
        ]

    def clean(self):
        if self.group_id and self.starts and self.ends:
            if GroupConsumption.objects.filter(group_id=self.group_id, starts__lt=self.ends, ends__gt=self.starts).exclude(pk=self.pk).exists():
                raise ValidationError('Для группы уже записаны кубы за пересекающийся период.')

    def save(self, *args, **kwargs):
        with transaction.atomic():
            WaterGroup.objects.select_for_update().get(pk=self.group_id)
            return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.group} · {self.starts} — {self.ends} · {self.volume} м³'

