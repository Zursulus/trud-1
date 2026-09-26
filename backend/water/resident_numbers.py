from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .models import User


RESIDENT_NUMBER_FIRST = 1
RESIDENT_NUMBER_LAST = 310
TEST_RESIDENT_NUMBER = 333


class ResidentNumberSlot(models.Model):
    """Stable human-facing resident number, deliberately separate from User.id.

    Slots 1..310 are reserved for real resident records. Slot 333 is reserved for
    the owner's test login. Technical Django primary keys must never be treated as
    these business-facing numbers.
    """

    PURPOSE_RESIDENT = 'resident'
    PURPOSE_TEST = 'test'
    PURPOSE_CHOICES = [
        (PURPOSE_RESIDENT, 'Житель'),
        (PURPOSE_TEST, 'Тестовая учётная запись'),
    ]

    number = models.PositiveSmallIntegerField('Номер пользователя', primary_key=True)
    purpose = models.CharField('Назначение', max_length=20, choices=PURPOSE_CHOICES)
    user = models.OneToOneField(
        User,
        verbose_name='Учётная запись',
        related_name='resident_number_slot',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
    )
    assigned_at = models.DateTimeField('Назначен', blank=True, null=True, editable=False)
    notes = models.CharField('Примечание', max_length=300, blank=True)

    class Meta:
        verbose_name = 'Номер пользователя'
        verbose_name_plural = 'Номера пользователей'
        ordering = ['number']

    def clean(self):
        if RESIDENT_NUMBER_FIRST <= self.number <= RESIDENT_NUMBER_LAST:
            if self.purpose != self.PURPOSE_RESIDENT:
                raise ValidationError({'purpose': 'Номера 1–310 зарезервированы для реальных пользователей.'})
        if self.number == TEST_RESIDENT_NUMBER and self.purpose != self.PURPOSE_TEST:
            raise ValidationError({'purpose': 'Номер 333 зарезервирован для тестовой учётной записи.'})
        if self.purpose == self.PURPOSE_TEST and self.number != TEST_RESIDENT_NUMBER:
            raise ValidationError({'number': 'Тестовая учётная запись использует номер 333.'})
        if self.user_id and self.user.is_staff:
            raise ValidationError({'user': 'Номер жителя нельзя назначать сотруднику административной части.'})

    def save(self, *args, **kwargs):
        if self.user_id and not self.assigned_at:
            self.assigned_at = timezone.now()
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        suffix = f' · {self.user.username}' if self.user_id else ' · свободен'
        return f'№{self.number}{suffix}'
