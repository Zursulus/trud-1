from datetime import date
from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
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
    commissioned_on = models.DateField('Установлен / принят на учёт', blank=True, null=True)
    seal_number = models.CharField('Номер пломбы', max_length=100, blank=True)
    retired_on = models.DateField('Снят с учёта', blank=True, null=True)
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Счётчик'
        verbose_name_plural = '05 · Счётчики'
        constraints = [models.UniqueConstraint(fields=['node', 'serial'], name='meter_node_serial')]

    def clean(self):
        if self.commissioned_on and self.retired_on and self.retired_on < self.commissioned_on:
            raise ValidationError('Дата снятия не может быть раньше даты установки.')
        if self.pk and self.reading_set.exists():
            original = Meter.objects.get(pk=self.pk)
            if any(getattr(original, key) != getattr(self, key) for key in ('node_id', 'group_id', 'account_id', 'kind')):
                raise ValidationError('Счётчик с показаниями нельзя переносить. Создайте отдельный счётчик.')
            if self.retired_on and self.reading_set.filter(date__gt=self.retired_on).exists():
                raise ValidationError('Есть показания позже даты снятия с учёта.')
            if self.commissioned_on and self.reading_set.filter(date__lt=self.commissioned_on).exists():
                raise ValidationError('Есть показания раньше даты установки счётчика.')
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
        permissions = [('export_reading', 'Может выгружать показания в CSV')]
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
        if self.meter.commissioned_on and self.date < self.meter.commissioned_on:
            raise ValidationError('Дата раньше установки счётчика.')
        if self.date > timezone.localdate():
            raise ValidationError('Нельзя записать показание будущей датой.')
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


class Person(RecordedModel):
    """A real person, deliberately separate from a plot and billing account."""
    full_name = models.CharField('ФИО', max_length=200)
    phone = models.CharField('Телефон', max_length=40, blank=True)
    email = models.EmailField('Электронная почта', blank=True)
    notes = models.TextField('Примечание', blank=True)
    archived = models.BooleanField('Архивная карточка человека', default=False)

    class Meta:
        verbose_name = 'Человек'
        verbose_name_plural = '02 · Люди'
        ordering = ['full_name', 'id']

    def clean(self):
        self.full_name = ' '.join(self.full_name.split())
        if not self.full_name:
            raise ValidationError({'full_name': 'Укажите ФИО.'})

    def __str__(self):
        return self.full_name


class LandPlot(RecordedModel):
    """Land plot. A billing account may be attached, but is not the plot itself."""
    label = models.CharField('Обозначение участка', max_length=100)
    address = models.CharField('Адрес / ориентир', max_length=200, blank=True)
    cadastral_number = models.CharField('Кадастровый номер', max_length=50, blank=True, null=True, unique=True)
    area_m2 = models.DecimalField(
        'Площадь, м²', max_digits=12, decimal_places=2, blank=True, null=True,
        validators=[MinValueValidator(Decimal('0.01'))],
    )
    account = models.ForeignKey(
        Account, verbose_name='Лицевой счёт', on_delete=models.PROTECT,
        related_name='land_plots', blank=True, null=True,
    )
    notes = models.TextField('Примечание', blank=True)
    archived = models.BooleanField('Архивный участок', default=False)

    class Meta:
        verbose_name = 'Участок'
        verbose_name_plural = '03 · Участки'
        ordering = ['label', 'id']

    def clean(self):
        self.label = ' '.join(self.label.split())
        if not self.label:
            raise ValidationError({'label': 'Укажите обозначение участка.'})
        self.cadastral_number = (self.cadastral_number or '').strip() or None

    def __str__(self):
        return self.label


class PlotRelation(RecordedModel):
    """Time-bounded owner or representative relation; records are never overwritten."""
    OWNER = 'owner'
    REPRESENTATIVE = 'representative'
    ROLE_CHOICES = [(OWNER, 'Собственник'), (REPRESENTATIVE, 'Представитель')]

    person = models.ForeignKey(Person, verbose_name='Человек', on_delete=models.PROTECT, related_name='plot_relations')
    plot = models.ForeignKey(LandPlot, verbose_name='Участок', on_delete=models.PROTECT, related_name='relations')
    role = models.CharField('Статус', max_length=20, choices=ROLE_CHOICES)
    starts = models.DateField('Действует с')
    ends = models.DateField('Действует до (не включая)', blank=True, null=True)
    document = models.CharField('Основание / документ', max_length=300, blank=True)
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Связь человека с участком'
        verbose_name_plural = '04 · Владение и представительство'
        ordering = ['plot', '-starts', 'id']
        constraints = [
            models.CheckConstraint(
                condition=Q(ends__isnull=True) | Q(ends__gt=models.F('starts')),
                name='plot_relation_dates',
            ),
        ]

    def clean(self):
        if not self.person_id or not self.plot_id or not self.starts:
            return
        overlaps = PlotRelation.objects.filter(
            person_id=self.person_id, plot_id=self.plot_id, role=self.role,
        ).filter(Q(ends__isnull=True) | Q(ends__gt=self.starts)).exclude(pk=self.pk)
        if self.ends:
            overlaps = overlaps.filter(starts__lt=self.ends)
        if overlaps.exists():
            raise ValidationError('У этого человека уже есть такая связь с участком на пересекающиеся даты.')

    def save(self, *args, **kwargs):
        with transaction.atomic():
            Person.objects.select_for_update().get(pk=self.person_id)
            LandPlot.objects.select_for_update().get(pk=self.plot_id)
            return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.person} — {self.get_role_display()} участка {self.plot}'


class BillingPolicy(RecordedModel):
    """Reusable billing behaviour; accounts may inherit or override it by date."""
    name = models.CharField('Название набора правил', max_length=200, unique=True)
    is_default = models.BooleanField('Правила по умолчанию', default=False)
    missing_reading = models.CharField('Если нет показаний', max_length=20, choices=[
        ('draft', 'Оставить черновик без суммы'),
        ('zero', 'Начислить нулевой расход'),
        ('average', 'Средний расход прошлых периодов'),
        ('previous', 'Расход прошлого периода'),
        ('norm', 'Норматив'),
        ('manual', 'Только ручной ввод'),
    ], default='draft')
    loss_distribution = models.CharField('Распределение общих потерь', max_length=30, choices=[
        ('none', 'Не распределять'),
        ('volume', 'Пропорционально расходу'),
        ('equal_account', 'Поровну по лицевым счетам'),
        ('equal_plot', 'Поровну по участкам'),
        ('area', 'Пропорционально площади'),
        ('manual', 'Ручное распределение'),
    ], default='none')
    rounding = models.CharField('Округление итоговой суммы', max_length=20, choices=[
        ('kopeck', 'До копейки'), ('ruble', 'До рубля'),
        ('up_kopeck', 'Вверх до копейки'), ('up_ruble', 'Вверх до рубля'),
        ('none', 'Не округлять до утверждения'),
    ], default='kopeck')
    payment_allocation = models.CharField('Распределение оплаты', max_length=20, choices=[
        ('oldest', 'Сначала старые долги'), ('current', 'Сначала текущий период'),
        ('manual', 'Только вручную'), ('reference', 'По назначению платежа'),
    ], default='oldest')
    average_periods = models.PositiveSmallIntegerField('Периодов для среднего', default=3)
    monthly_norm_m3 = models.DecimalField(
        'Норматив, м³ за месяц', max_digits=12, decimal_places=3,
        blank=True, null=True, validators=[MinValueValidator(Decimal('0'))],
    )
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Набор правил начисления'
        verbose_name_plural = '08 · Правила начислений'
        constraints = [
            models.UniqueConstraint(
                fields=['is_default'], condition=Q(is_default=True),
                name='single_default_billing_policy',
            ),
        ]

    def clean(self):
        if self.missing_reading == 'norm' and self.monthly_norm_m3 is None:
            raise ValidationError({'monthly_norm_m3': 'Для начисления по нормативу укажите объём.'})

    def __str__(self):
        return self.name


class BillingAssignment(RecordedModel):
    """Date-bounded policy assignment: account beats group, group beats default."""
    policy = models.ForeignKey(BillingPolicy, verbose_name='Набор правил', on_delete=models.PROTECT)
    account = models.ForeignKey(Account, verbose_name='Лицевой счёт', on_delete=models.PROTECT, blank=True, null=True)
    group = models.ForeignKey(WaterGroup, verbose_name='Группа', on_delete=models.PROTECT, blank=True, null=True)
    starts = models.DateField('Действует с')
    ends = models.DateField('Действует до (не включая)', blank=True, null=True)
    priority = models.PositiveSmallIntegerField('Приоритет', default=100)
    notes = models.TextField('Причина / примечание', blank=True)

    class Meta:
        verbose_name = 'Назначение правил'
        verbose_name_plural = '09 · Назначения правил'
        constraints = [
            models.CheckConstraint(condition=Q(ends__isnull=True) | Q(ends__gt=models.F('starts')), name='billing_assignment_dates'),
            models.CheckConstraint(condition=Q(account__isnull=False) | Q(group__isnull=False), name='billing_assignment_scope'),
        ]

    def clean(self):
        if bool(self.account_id) == bool(self.group_id):
            raise ValidationError('Укажите либо лицевой счёт, либо группу.')
        if not self.starts:
            return
        scope = {'account_id': self.account_id} if self.account_id else {'group_id': self.group_id}
        overlaps = BillingAssignment.objects.filter(**scope, starts__lt=self.ends or date.max).filter(
            Q(ends__isnull=True) | Q(ends__gt=self.starts), priority=self.priority,
        ).exclude(pk=self.pk)
        if overlaps.exists():
            raise ValidationError('Для этого объекта уже назначены правила с тем же приоритетом на пересекающиеся даты.')

    def __str__(self):
        return f'{self.account or self.group} → {self.policy}'


class Tariff(RecordedModel):
    name = models.CharField('Название тарифа', max_length=200)
    rate = models.DecimalField('Цена за 1 м³', max_digits=12, decimal_places=4, validators=[MinValueValidator(Decimal('0'))])
    starts = models.DateField('Действует с')
    ends = models.DateField('Действует до (не включая)', blank=True, null=True)
    account = models.ForeignKey(Account, verbose_name='Только для лицевого счёта', on_delete=models.PROTECT, blank=True, null=True)
    group = models.ForeignKey(WaterGroup, verbose_name='Только для группы', on_delete=models.PROTECT, blank=True, null=True)
    notes = models.TextField('Основание / документ', blank=True)

    class Meta:
        verbose_name = 'Тариф'
        verbose_name_plural = '10 · Тарифы'
        constraints = [models.CheckConstraint(condition=Q(ends__isnull=True) | Q(ends__gt=models.F('starts')), name='tariff_dates')]

    def clean(self):
        if self.account_id and self.group_id:
            raise ValidationError('Тариф может быть общим, групповым или индивидуальным, но не двумя сразу.')
        if not self.starts:
            return
        scope = {'account_id': self.account_id, 'group_id': self.group_id}
        overlaps = Tariff.objects.filter(**scope, starts__lt=self.ends or date.max).filter(
            Q(ends__isnull=True) | Q(ends__gt=self.starts),
        ).exclude(pk=self.pk)
        if overlaps.exists():
            raise ValidationError('Тарифы одного уровня не должны пересекаться по датам.')

    def __str__(self):
        scope = self.account or self.group or 'все лицевые счета'
        return f'{self.name}: {self.rate} ₽/м³ · {scope}'


class BillingPeriod(RecordedModel):
    starts = models.DateField('Начало периода')
    ends = models.DateField('Конец периода (не включая)')
    status = models.CharField('Состояние', max_length=20, choices=[
        ('open', 'Открыт'), ('calculated', 'Черновики рассчитаны'),
        ('approved', 'Утверждён'), ('closed', 'Закрыт'),
    ], default='open')
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Расчётный период'
        verbose_name_plural = '11 · Расчётные периоды'
        ordering = ['-starts']
        constraints = [
            models.CheckConstraint(condition=Q(ends__gt=models.F('starts')), name='billing_period_dates'),
            models.UniqueConstraint(fields=['starts', 'ends'], name='billing_period_unique'),
        ]

    def clean(self):
        if self.pk:
            original = BillingPeriod.objects.get(pk=self.pk)
            if original.status == 'closed' and any(
                getattr(original, field) != getattr(self, field) for field in ('starts', 'ends', 'status')
            ):
                raise ValidationError('Закрытый период нельзя изменять. Исправление оформляется следующим периодом.')
        if self.starts and self.ends and BillingPeriod.objects.filter(
            starts__lt=self.ends, ends__gt=self.starts,
        ).exclude(pk=self.pk).exists():
            raise ValidationError('Расчётные периоды не должны пересекаться.')

    def __str__(self):
        return f'{self.starts:%d.%m.%Y}–{self.ends:%d.%m.%Y}'


class Charge(RecordedModel):
    account = models.ForeignKey(Account, verbose_name='Лицевой счёт', on_delete=models.PROTECT)
    period = models.ForeignKey(BillingPeriod, verbose_name='Расчётный период', on_delete=models.PROTECT)
    kind = models.CharField('Вид начисления', max_length=20, choices=[
        ('water', 'Вода по потреблению'), ('loss', 'Общие потери'),
        ('norm', 'По нормативу'), ('adjustment', 'Корректировка'),
        ('service', 'Услуга / иной платёж'), ('opening', 'Начальный долг / переплата'),
    ])
    volume = models.DecimalField(
        'Объём, м³', max_digits=14, decimal_places=3, blank=True, null=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    rate = models.DecimalField(
        'Тариф', max_digits=12, decimal_places=4, blank=True, null=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    amount = models.DecimalField('Сумма, ₽', max_digits=14, decimal_places=2)
    status = models.CharField('Состояние', max_length=20, choices=[
        ('draft', 'Черновик'), ('approved', 'Утверждено'), ('cancelled', 'Отменено'),
    ], default='draft')
    origin = models.CharField('Источник', max_length=20, choices=[
        ('manual', 'Ручной ввод'), ('calculation', 'Автоматический расчёт'),
        ('import', 'Импорт'),
    ], default='manual')
    source_key = models.CharField('Ключ автоматического расчёта', max_length=200, blank=True, null=True, unique=True, editable=False)
    calculation = models.TextField('Расшифровка расчёта', blank=True)
    notes = models.TextField('Основание / примечание', blank=True)

    class Meta:
        verbose_name = 'Начисление'
        verbose_name_plural = '12 · Начисления'
        ordering = ['-period__starts', 'account', 'id']

    def clean(self):
        if self.pk:
            original = Charge.objects.get(pk=self.pk)
            protected = ('account_id', 'period_id', 'kind', 'volume', 'rate', 'amount', 'origin', 'source_key')
            if original.status == 'approved' and any(getattr(original, field) != getattr(self, field) for field in protected):
                raise ValidationError('Утверждённое начисление нельзя переписывать. Создайте корректировку.')
        if (self.volume is None) != (self.rate is None):
            raise ValidationError('Объём и тариф указываются вместе либо оба не указываются.')
        if self.kind not in ('adjustment', 'opening') and self.amount < 0:
            raise ValidationError({'amount': 'Обычное начисление не может быть отрицательным.'})

    def __str__(self):
        return f'{self.account} · {self.get_kind_display()} · {self.amount} ₽'


class Payment(RecordedModel):
    account = models.ForeignKey(Account, verbose_name='Лицевой счёт', on_delete=models.PROTECT)
    paid_on = models.DateField('Дата оплаты')
    amount = models.DecimalField('Сумма, ₽', max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    method = models.CharField('Способ', max_length=20, choices=[
        ('bank', 'Банковская выписка'), ('receipt', 'Квитанция'),
        ('cash', 'Наличные'), ('card', 'Карта / эквайринг'), ('other', 'Другое'),
    ])
    reference = models.CharField('Номер / назначение платежа', max_length=300, blank=True)
    status = models.CharField('Состояние', max_length=20, choices=[
        ('pending', 'Ожидает проверки'), ('confirmed', 'Подтверждён'), ('reversed', 'Отменён / возвращён'),
    ], default='pending')
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Оплата'
        verbose_name_plural = '13 · Оплаты'
        ordering = ['-paid_on', '-id']

    def clean(self):
        if self.pk:
            original = Payment.objects.get(pk=self.pk)
            protected = ('account_id', 'paid_on', 'amount', 'method', 'reference')
            if original.status == 'confirmed' and any(getattr(original, field) != getattr(self, field) for field in protected):
                raise ValidationError('Подтверждённую оплату нельзя переписывать. Отмените её и создайте новую запись.')

    def __str__(self):
        return f'{self.account} · {self.paid_on:%d.%m.%Y} · {self.amount} ₽'


class PaymentAllocation(RecordedModel):
    payment = models.ForeignKey(Payment, verbose_name='Оплата', on_delete=models.PROTECT, related_name='allocations')
    charge = models.ForeignKey(Charge, verbose_name='Начисление', on_delete=models.PROTECT, related_name='allocations')
    amount = models.DecimalField('Зачтено, ₽', max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])

    class Meta:
        verbose_name = 'Распределение оплаты'
        verbose_name_plural = '14 · Распределение оплат'
        constraints = [models.UniqueConstraint(fields=['payment', 'charge'], name='payment_charge_unique')]

    def clean(self):
        if self.payment_id and self.charge_id and self.payment.account_id != self.charge.account_id:
            raise ValidationError('Оплату можно зачесть только на начисления того же лицевого счёта.')
        if self.payment_id and self.amount:
            allocated = PaymentAllocation.objects.filter(payment_id=self.payment_id).exclude(pk=self.pk).aggregate(
                total=models.Sum('amount'),
            )['total'] or Decimal('0')
            if allocated + self.amount > self.payment.amount:
                raise ValidationError('Распределённая сумма превышает размер оплаты.')

    def __str__(self):
        return f'{self.payment} → {self.charge}: {self.amount} ₽'
