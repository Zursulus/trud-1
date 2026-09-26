from pathlib import Path
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .models import Person, RecordedModel, ResidentAppeal, ResidentAppealMessage, User


APPEAL_ATTACHMENT_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}
APPEAL_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024


class ResidentIdentity(RecordedModel):
    """Verified link between one resident login and one real person.

    This model is deliberately separate from ResidentAccess: identifying who a
    login belongs to must not by itself grant access to any plot or account.
    """

    user = models.OneToOneField(
        User, verbose_name='Кабинет', on_delete=models.PROTECT,
        related_name='resident_identity',
    )
    person = models.OneToOneField(
        Person, verbose_name='Человек', on_delete=models.PROTECT,
        related_name='resident_identity',
    )
    verified_at = models.DateTimeField('Личность подтверждена', default=timezone.now, editable=False)
    verified_by = models.ForeignKey(
        User, verbose_name='Кто подтвердил', on_delete=models.PROTECT,
        related_name='verified_resident_identities', blank=True, null=True,
    )
    basis = models.CharField('Основание подтверждения', max_length=300, blank=True)
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Идентичность кабинета жителя'
        verbose_name_plural = 'Идентичности кабинетов жителей'
        ordering = ['person', 'id']

    def clean(self):
        if self.user_id and self.user.is_staff:
            raise ValidationError({'user': 'Сотрудника нельзя связывать с кабинетом жителя.'})
        if self.verified_by_id and not self.verified_by.is_staff:
            raise ValidationError({'verified_by': 'Подтвердить личность может только сотрудник.'})

    def __str__(self):
        return f'{self.user} ↔ {self.person}'


class TsnMembership(RecordedModel):
    """Time-bounded TSN membership, independent of plot ownership and portal access."""

    END_VOLUNTARY = 'voluntary'
    END_RIGHT_ENDED = 'right_ended'
    END_DEATH = 'death'
    END_EXCLUSION = 'exclusion'
    END_REASON_CHOICES = [
        (END_VOLUNTARY, 'Добровольный выход'),
        (END_RIGHT_ENDED, 'Прекращение права на участок'),
        (END_DEATH, 'Смерть'),
        (END_EXCLUSION, 'Исключение'),
    ]

    person = models.ForeignKey(
        Person, verbose_name='Член ТСН', on_delete=models.PROTECT,
        related_name='tsn_memberships',
    )
    application_on = models.DateField('Дата заявления')
    starts = models.DateField('Членство с')
    decision_ref = models.CharField('Решение правления / основание приёма', max_length=300)
    member_document = models.CharField('Документ о членстве', max_length=300, blank=True)
    ends = models.DateField('Членство прекращено (дата)', blank=True, null=True)
    end_reason = models.CharField(
        'Основание прекращения', max_length=20,
        choices=END_REASON_CHOICES, blank=True,
    )
    end_document = models.CharField('Документ о прекращении', max_length=300, blank=True)
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Членство в ТСН'
        verbose_name_plural = 'Членство в ТСН'
        ordering = ['person', '-starts', 'id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends__isnull=True) | models.Q(ends__gt=models.F('starts')),
                name='tsn_membership_dates',
            ),
            models.UniqueConstraint(
                fields=['person'], condition=models.Q(ends__isnull=True),
                name='one_active_tsn_membership_per_person',
            ),
        ]

    def clean(self):
        if not self.person_id or not self.application_on or not self.starts:
            return
        if self.starts < self.application_on:
            raise ValidationError({'starts': 'Дата начала членства не может быть раньше заявления.'})
        if self.ends and not self.end_reason:
            raise ValidationError({'end_reason': 'Для прекращённого членства укажите основание.'})
        if not self.ends and self.end_reason:
            raise ValidationError({'end_reason': 'Основание прекращения указывают вместе с датой прекращения.'})
        overlaps = TsnMembership.objects.filter(person_id=self.person_id).filter(
            models.Q(ends__isnull=True) | models.Q(ends__gt=self.starts),
        ).exclude(pk=self.pk)
        if self.ends:
            overlaps = overlaps.filter(starts__lt=self.ends)
        if overlaps.exists():
            raise ValidationError('У человека уже есть членство ТСН на пересекающиеся даты.')

    @property
    def is_active(self):
        today = timezone.localdate()
        return self.starts <= today and (self.ends is None or self.ends > today)

    def __str__(self):
        return f'{self.person} · с {self.starts:%d.%m.%Y}'


def appeal_attachment_path(instance, filename):
    suffix = Path(filename).suffix.lower()[:12]
    return f'appeal-attachments/{timezone.localdate():%Y/%m}/{uuid.uuid4().hex}{suffix}'


class ResidentAppealBoardMessage(models.Model):
    """Immutable staff message in a resident conversation."""

    appeal = models.ForeignKey(
        ResidentAppeal, verbose_name='Обращение', on_delete=models.PROTECT,
        related_name='board_messages',
    )
    author = models.ForeignKey(
        User, verbose_name='Сотрудник', on_delete=models.PROTECT,
        related_name='board_appeal_messages',
    )
    body = models.TextField('Сообщение правления', max_length=5000)
    created_at = models.DateTimeField('Отправлено', default=timezone.now, editable=False)

    class Meta:
        verbose_name = 'Сообщение правления по обращению'
        verbose_name_plural = 'Сообщения правления по обращениям'
        ordering = ['created_at', 'id']

    def clean(self):
        if self.author_id and not self.author.is_staff:
            raise ValidationError({'author': 'Ответ правления может отправить только сотрудник.'})
        if self.pk:
            stored = type(self).objects.get(pk=self.pk)
            if any(
                getattr(stored, field) != getattr(self, field)
                for field in ('appeal_id', 'author_id', 'body')
            ):
                raise ValidationError('Отправленное сообщение нельзя переписывать. Добавьте новое сообщение.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.appeal} · {self.created_at:%d.%m.%Y %H:%M}'


class ResidentAppealAttachment(models.Model):
    """Private file attached to a resident/board conversation.

    Files are never exposed through MEDIA_URL. Both resident and staff downloads
    must pass an explicit appeal permission check in a Django view.
    """

    appeal = models.ForeignKey(
        ResidentAppeal, verbose_name='Обращение', on_delete=models.PROTECT,
        related_name='attachments',
    )
    message = models.ForeignKey(
        ResidentAppealMessage, verbose_name='Сообщение жителя', on_delete=models.PROTECT,
        related_name='attachments', blank=True, null=True,
    )
    board_message = models.ForeignKey(
        ResidentAppealBoardMessage, verbose_name='Сообщение правления', on_delete=models.PROTECT,
        related_name='attachments', blank=True, null=True,
    )
    uploaded_by = models.ForeignKey(
        User, verbose_name='Загрузил', on_delete=models.PROTECT,
        related_name='resident_appeal_attachments',
    )
    document = models.FileField(
        'Файл', upload_to=appeal_attachment_path, max_length=300,
    )
    original_name = models.CharField('Исходное имя', max_length=255, editable=False)
    file_size = models.PositiveBigIntegerField('Размер, байт', editable=False)
    created_at = models.DateTimeField('Загружено', default=timezone.now, editable=False)

    class Meta:
        verbose_name = 'Вложение обращения'
        verbose_name_plural = 'Вложения обращений'
        ordering = ['created_at', 'id']

    def clean(self):
        if self.message_id and self.board_message_id:
            raise ValidationError('Вложение относится только к одному сообщению.')
        if self.message_id and self.appeal_id and self.message.appeal_id != self.appeal_id:
            raise ValidationError({'message': 'Сообщение относится к другому обращению.'})
        if self.board_message_id and self.appeal_id and self.board_message.appeal_id != self.appeal_id:
            raise ValidationError({'board_message': 'Сообщение правления относится к другому обращению.'})
        if self.uploaded_by_id and self.appeal_id:
            if not self.uploaded_by.is_staff and self.appeal.author_id != self.uploaded_by_id:
                raise ValidationError({'uploaded_by': 'Житель может прикладывать файл только к своему обращению.'})
            if self.board_message_id and self.uploaded_by_id != self.board_message.author_id:
                raise ValidationError({'uploaded_by': 'Вложение правления должно принадлежать автору сообщения.'})
        if self.document:
            size = getattr(self.document, 'size', 0)
            if size > APPEAL_ATTACHMENT_MAX_BYTES:
                raise ValidationError({'document': 'Файл должен быть не больше 10 МБ.'})
            suffix = Path(self.document.name).suffix.lower()
            if suffix not in APPEAL_ATTACHMENT_EXTENSIONS:
                raise ValidationError({'document': 'Разрешены только PDF, JPG и PNG.'})
        if self.pk:
            stored = type(self).objects.get(pk=self.pk)
            protected = ('appeal_id', 'message_id', 'board_message_id', 'uploaded_by_id', 'document')
            for field in protected:
                old = getattr(stored, field)
                new = getattr(self, field)
                if field == 'document':
                    old = getattr(old, 'name', old)
                    new = getattr(new, 'name', new)
                if old != new:
                    raise ValidationError('Сохранённое вложение нельзя подменить. Добавьте новый файл.')

    def save(self, *args, **kwargs):
        if self.document and (self._state.adding or not self.original_name):
            self.original_name = Path(self.document.name).name[:255]
            self.file_size = self.document.size
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def is_board_file(self):
        return bool(self.uploaded_by_id and self.uploaded_by.is_staff)

    def __str__(self):
        return f'{self.appeal} · {self.original_name or self.document.name}'


class ResidentAppealViewState(models.Model):
    """Per-resident read marker; operational metadata, not an audited business record."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='appeal_view_states')
    appeal = models.ForeignKey(ResidentAppeal, on_delete=models.CASCADE, related_name='view_states')
    last_seen_response_at = models.DateTimeField(blank=True, null=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'appeal'], name='resident_appeal_view_state_unique'),
        ]

    def clean(self):
        if self.user_id and self.appeal_id and self.appeal.author_id != self.user_id:
            raise ValidationError('Статус просмотра принадлежит только автору обращения.')

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)
