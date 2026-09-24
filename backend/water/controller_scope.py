from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q

from .models import RecordedModel, User, WaterGroup


class ControllerLineAccess(RecordedModel):
    """Date-bounded assignment of one line/group to its responsible controller."""

    user = models.ForeignKey(
        User,
        verbose_name='Старший / контролёр',
        on_delete=models.PROTECT,
        related_name='water_line_accesses',
    )
    group = models.ForeignKey(
        WaterGroup,
        verbose_name='Линия / группа',
        on_delete=models.PROTECT,
        related_name='controller_accesses',
    )
    starts = models.DateField('Доступ с')
    ends = models.DateField('Доступ до (не включая)', blank=True, null=True)
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Доступ старшего к линии'
        verbose_name_plural = 'Доступ старших к линиям'
        ordering = ['group', '-starts', 'id']
        permissions = [
            ('use_controller_workspace', 'Может вносить показания закреплённых линий'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(ends__isnull=True) | Q(ends__gt=models.F('starts')),
                name='controller_line_access_dates',
            ),
        ]

    def clean(self):
        if self.user_id and not self.user.is_staff:
            raise ValidationError({'user': 'Контролёр должен быть сотрудником с доступом в рабочую панель.'})
        if not self.group_id or not self.starts:
            return
        overlaps = ControllerLineAccess.objects.filter(group_id=self.group_id).exclude(pk=self.pk)
        overlaps = overlaps.filter(Q(ends__isnull=True) | Q(ends__gt=self.starts))
        if self.ends:
            overlaps = overlaps.filter(starts__lt=self.ends)
        if overlaps.exists():
            raise ValidationError({'group': 'На пересекающиеся даты у этой линии уже назначен старший / контролёр.'})

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.user_id:
                User.objects.select_for_update().get(pk=self.user_id)
            if self.group_id:
                WaterGroup.objects.select_for_update().get(pk=self.group_id)
            return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.group} → {self.user}'
