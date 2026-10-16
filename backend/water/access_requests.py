from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from simple_history.models import HistoricalRecords

from .models import Account, ResidentAccess, ResidentInvite, User


class ResidentAccessRequest(models.Model):
    """Closed request for portal access.

    Submission is intentionally not proof of identity, ownership, membership or
    portal authority. A request may only be approved by staff after a separate
    private-registry/document check. Legacy requests create an invite; signed-in
    residents can receive an explicit PortalGrant after manual verification.
    """

    STATUS_NEW = 'new'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_NEW, 'Новая'),
        (STATUS_APPROVED, 'Одобрена'),
        (STATUS_REJECTED, 'Отклонена'),
    ]
    CLAIM_OWNER = 'owner'
    CLAIM_REPRESENTATIVE = 'representative'
    CLAIM_PAYER = 'payer'
    CLAIM_OTHER = 'other'
    CLAIM_CHOICES = [
        (CLAIM_OWNER, 'Собственник'),
        (CLAIM_REPRESENTATIVE, 'Представитель'),
        (CLAIM_PAYER, 'Плательщик'),
        (CLAIM_OTHER, 'Другое основание'),
    ]

    history = HistoricalRecords()
    last_reading = models.DecimalField('Последнее показание для сверки', max_digits=12, decimal_places=3, null=True, blank=True)
    consent_record = models.ForeignKey('forum.ConsentRecord', on_delete=models.PROTECT,
        null=True, blank=True, editable=False)
    grant = models.OneToOneField('water.PortalGrant', on_delete=models.PROTECT,
        null=True, blank=True, editable=False, related_name='access_request')

    full_name = models.CharField('Как к вам обращаться', max_length=200)
    email = models.EmailField('Электронная почта')
    phone = models.CharField('Телефон', max_length=40, blank=True)
    plot_hint = models.CharField('Участок / адрес', max_length=200)
    claimed_role = models.CharField('Основание запроса', max_length=20, choices=CLAIM_CHOICES)
    message = models.TextField('Комментарий', max_length=1000, blank=True)
    submitted_at = models.DateTimeField('Получено', default=timezone.now, editable=False)
    submission_key = models.CharField(max_length=64, db_index=True, editable=False)
    requester_user = models.ForeignKey(
        User, verbose_name='Кабинет-заявитель', on_delete=models.PROTECT,
        related_name='submitted_resident_access_requests', blank=True, null=True, editable=False,
    )

    status = models.CharField('Решение', max_length=20, choices=STATUS_CHOICES, default=STATUS_NEW, editable=False)
    matched_account = models.ForeignKey(
        Account, verbose_name='Проверенный лицевой счёт', on_delete=models.PROTECT,
        related_name='resident_access_requests', blank=True, null=True, editable=False,
    )
    approved_role = models.CharField(
        'Одобренное основание доступа', max_length=20,
        choices=ResidentAccess._meta.get_field('role').choices, blank=True, editable=False,
    )
    decision_note = models.TextField('Служебное основание решения', max_length=1000, blank=True, editable=False)
    decided_by = models.ForeignKey(
        User, verbose_name='Решил', on_delete=models.PROTECT,
        related_name='decided_resident_access_requests', blank=True, null=True, editable=False,
    )
    decided_at = models.DateTimeField('Решено', blank=True, null=True, editable=False)
    invite = models.OneToOneField(
        ResidentInvite, verbose_name='Созданное приглашение', on_delete=models.PROTECT,
        related_name='access_request', blank=True, null=True, editable=False,
    )

    class Meta:
        verbose_name = 'Запрос доступа к кабинету'
        verbose_name_plural = 'Запросы доступа к кабинету'
        ordering = ['status', '-submitted_at', '-id']

    def clean(self):
        self.full_name = ' '.join((self.full_name or '').split())
        self.email = (self.email or '').strip().lower()
        self.phone = ' '.join((self.phone or '').split())
        self.plot_hint = ' '.join((self.plot_hint or '').split())
        self.message = (self.message or '').strip()

        if self.last_reading is not None and self.last_reading < 0:
            raise ValidationError({'last_reading': 'Показание не может быть отрицательным.'})
        if self.consent_record_id and (
            self.consent_record.user_id != self.requester_user_id
            or self.consent_record.document_type != 'binding' or self.consent_record.action != 'accept'
        ):
            raise ValidationError('Нужно отдельное согласие этого заявителя на данные заявки.')
        if self.matched_account_id and self.matched_account.archived:
            raise ValidationError({'matched_account': 'Нельзя выдавать доступ к архивному лицевому счёту.'})
        if self.status == self.STATUS_APPROVED:
            if (not self.matched_account_id or not (self.invite_id or self.grant_id)
                    or (self.invite_id and not self.approved_role)):
                raise ValidationError('Нужны проверенный счёт и приглашение с ролью либо явный доступ.')
            if not self.decided_by_id or not self.decided_at:
                raise ValidationError('У решения должны быть автор и время.')
        elif self.status == self.STATUS_REJECTED:
            if not self.decided_by_id or not self.decided_at:
                raise ValidationError('У решения должны быть автор и время.')
            if self.invite_id or self.grant_id:
                raise ValidationError('У отклонённой заявки не может быть приглашения.')
        else:
            if any((self.matched_account_id, self.approved_role, self.decision_note, self.decided_by_id, self.decided_at, self.invite_id, self.grant_id)):
                raise ValidationError('Новая заявка не должна содержать решение.')

        if self.pk:
            stored = type(self).objects.get(pk=self.pk)
            immutable = ('full_name', 'email', 'phone', 'plot_hint', 'claimed_role', 'message', 'submission_key', 'last_reading', 'consent_record_id')
            if any(getattr(stored, field) != getattr(self, field) for field in immutable) or (
                stored.requester_user_id != self.requester_user_id
            ):
                raise ValidationError('Исходную заявку нельзя переписывать. Создайте служебное решение отдельно.')
            if stored.status != self.STATUS_NEW and any(
                getattr(stored, field) != getattr(self, field)
                for field in ('status', 'matched_account_id', 'approved_role', 'decision_note', 'decided_by_id', 'decided_at', 'invite_id', 'grant_id')
            ):
                raise ValidationError('Принятое решение по заявке нельзя переписывать.')
            if stored.status == self.STATUS_NEW and self.status not in (
                self.STATUS_NEW, self.STATUS_APPROVED, self.STATUS_REJECTED,
            ):
                raise ValidationError('Недопустимый переход состояния заявки.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'Запрос №{self.pk or "—"} · {self.submitted_at:%d.%m.%Y %H:%M}'
