from django.core.exceptions import ValidationError
from django.db import models

from .models import Account, Person
from .resident_numbers import (
    RESIDENT_NUMBER_FIRST,
    RESIDENT_NUMBER_LAST,
    ResidentNumberSlot,
)


class MemberRegistryEntry(models.Model):
    """Closed record keyed by the stable human-facing resident number.

    The ordinary portal works with ResidentNumberSlot.number and Account. Personal
    contacts live here behind the explicit private-registry permission. A Person
    link is optional legacy/legal metadata: new records do not need an FIO merely
    to exist in the registry.
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
        verbose_name='Человек / ФИО (если юридически необходимо)',
        related_name='member_registry_entry',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
    )
    account = models.ForeignKey(
        Account,
        verbose_name='Рабочий лицевой счёт',
        related_name='member_registry_entries',
        on_delete=models.PROTECT,
        blank=True,
        null=True,
    )
    phone = models.CharField('Телефон', max_length=160, blank=True)
    email = models.EmailField('Электронная почта', blank=True)
    joined_year = models.PositiveSmallIntegerField('Год вступления', blank=True, null=True)
    membership_note = models.CharField('Основание / исходная пометка', max_length=500, blank=True)
    created_at = models.DateTimeField('Связь создана', auto_now_add=True, editable=False)

    class Meta:
        verbose_name = 'Запись закрытого реестра'
        verbose_name_plural = 'Закрытый реестр · номера и контакты'
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
            raise ValidationError({'resident_number': 'Тестовый номер нельзя помещать в реестр реальных членов.'})
        if self.person_id and self.person.archived:
            raise ValidationError({'person': 'Нельзя создавать новую связь с архивной карточкой человека.'})
        if self.account_id and self.account.archived:
            raise ValidationError({'account': 'Нельзя связывать реестр с архивным лицевым счётом.'})
        if self.joined_year is not None and not (1900 <= self.joined_year <= 2100):
            raise ValidationError({'joined_year': 'Проверьте год вступления.'})
        if not self._state.adding:
            stored = type(self).objects.get(pk=self.pk)
            if stored.person_id and stored.person_id != self.person_id:
                raise ValidationError('Связь с человеком нельзя переписывать автоматически.')
            if stored.account_id and stored.account_id != self.account_id:
                raise ValidationError('Связь № пользователя с лицевым счётом нельзя переписывать автоматически.')

    def save(self, *args, **kwargs):
        self.phone = ' '.join((self.phone or '').split())
        self.email = (self.email or '').strip().lower()
        self.membership_note = ' '.join((self.membership_note or '').split())
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'№{self.resident_number_id}'
