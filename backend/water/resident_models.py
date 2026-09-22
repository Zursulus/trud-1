from pathlib import Path
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from .models import ResidentAppeal, ResidentAppealMessage, User


APPEAL_ATTACHMENT_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png'}
APPEAL_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024


def appeal_attachment_path(instance, filename):
    suffix = Path(filename).suffix.lower()[:12]
    return f'appeal-attachments/{timezone.localdate():%Y/%m}/{uuid.uuid4().hex}{suffix}'


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
        if self.message_id and self.appeal_id and self.message.appeal_id != self.appeal_id:
            raise ValidationError({'message': 'Сообщение относится к другому обращению.'})
        if self.uploaded_by_id and self.appeal_id:
            if not self.uploaded_by.is_staff and self.appeal.author_id != self.uploaded_by_id:
                raise ValidationError({'uploaded_by': 'Житель может прикладывать файл только к своему обращению.'})
        if self.document:
            size = getattr(self.document, 'size', 0)
            if size > APPEAL_ATTACHMENT_MAX_BYTES:
                raise ValidationError({'document': 'Файл должен быть не больше 10 МБ.'})
            suffix = Path(self.document.name).suffix.lower()
            if suffix not in APPEAL_ATTACHMENT_EXTENSIONS:
                raise ValidationError({'document': 'Разрешены только PDF, JPG и PNG.'})
        if self.pk:
            stored = type(self).objects.get(pk=self.pk)
            protected = ('appeal_id', 'message_id', 'uploaded_by_id', 'document')
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
