from django.core.exceptions import ValidationError
from django.db import models

from .models import Person
from .resident_numbers import (
    RESIDENT_NUMBER_FIRST,
    RESIDENT_NUMBER_LAST,
    ResidentNumberSlot,
)


class MemberRegistryEntry(models.Model):
    """Closed mapping between a human-facing resident number and a real person.

    The working portal must use ResidentNumberSlot.number and must not need this
    table. This table is deliberately small: personal/contact/legal details stay
    on Person and PlotRelation behind the same private-registry permission.
    """

    resident_number = models.OneToOneField(
        ResidentNumberSlot,
        verbose_name='№ пользователя',
        related_name='member_registry_entry',
        on_delete=models.PROTECT,
        primary_key=True,
    )
    person = models.OneToOneField(
        Person,
        verbose_name='Человек',
        related_name='member_registry_entry',
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField('Связь создана', auto_now_add=True, editable=False)

    class Meta:
        verbose_name = 'Связь № пользователя с человеком'
        verbose_name_plural = 'Закрытый реестр · номера и люди'
        ordering = ['resident_number_id']
        permissions = [
            ('access_private_registry', 'Может работать с закрытым реестром членов ТСН'),
        ]

    def clean(self):
        if not self.resident_number_id:
            return
        slot = self.resident_number
        if not (RESIDENT_NUMBER_FIRST <= slot.number <= RESIDENT_NUMBER_LAST):
            raise ValidationError({'resident_number': 'Закрытый реестр использует только номера 1–310.'})
        if slot.purpose != ResidentNumberSlot.PURPOSE_RESIDENT:
            raise ValidationError({'resident_number': 'Тестовый номер нельзя связывать с реальным человеком.'})
        if self.person_id and self.person.archived:
            raise ValidationError({'person': 'Нельзя создавать новую связь с архивной карточкой человека.'})
        if not self._state.adding:
            stored = type(self).objects.get(pk=self.pk)
            if stored.person_id != self.person_id:
                raise ValidationError('Связь № пользователя с человеком нельзя переписывать. Проведите отдельную проверяемую операцию.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'№{self.resident_number_id}'
