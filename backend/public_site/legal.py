"""Versioned legal documents. Published revisions are never overwritten."""
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Записи можно только добавлять.')

    def delete(self):
        raise ValidationError('Записи нельзя удалять.')

    def bulk_create(self, objs, **kwargs):
        if kwargs.get('update_conflicts'):
            raise ValidationError('Существующие записи нельзя заменять.')
        return super().bulk_create(objs, **kwargs)

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('Записи можно только добавлять.')


class AppendOnlyModel(models.Model):
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding or kwargs.get('force_update') or kwargs.get('update_fields'):
            raise ValidationError('Запись неизменяема. Добавьте новую запись.')
        self.full_clean()
        kwargs['force_insert'] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Записи нельзя удалять.')


class LegalDocument(AppendOnlyModel):
    KINDS = [('privacy', 'Политика обработки персональных данных'),
             ('consent', 'Согласие на обработку персональных данных'),
             ('rules', 'Правила форума')]
    kind = models.CharField('Документ', max_length=20, choices=KINDS)
    version = models.CharField('Версия', max_length=40)
    effective_on = models.DateField('Дата действия', default=timezone.localdate)
    body = models.TextField('Текст')
    created_at = models.DateTimeField('Добавлено', auto_now_add=True)

    class Meta:
        verbose_name = 'Версия юридического документа'
        verbose_name_plural = 'Юридические документы'
        ordering = ['kind', '-effective_on', '-id']
        constraints = [models.UniqueConstraint(fields=['kind', 'version'], name='legal_kind_version')]

    def __str__(self):
        return f'{self.get_kind_display()} · {self.version}'


def current_document(kind):
    return LegalDocument.objects.filter(kind=kind, effective_on__lte=timezone.localdate()).order_by(
        '-effective_on', '-pk',
    ).first()
