from pathlib import Path
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


PUBLIC_DOCUMENT_EXTENSIONS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.odt', '.ods', '.txt'}


def public_document_path(instance, filename):
    suffix = Path(filename).suffix.lower()[:12]
    return f'public-documents/{timezone.localdate():%Y/%m}/{uuid.uuid4().hex}{suffix}'


class PublicDocumentCategory(models.Model):
    name = models.CharField('Категория', max_length=120, unique=True)
    active = models.BooleanField('Можно выбирать', default=True)
    sort_order = models.PositiveIntegerField('Порядок', default=100)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        verbose_name = 'Категория публичного документа'
        verbose_name_plural = 'Категории публичных документов'
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class PublicNews(models.Model):
    title = models.CharField('Заголовок', max_length=180)
    category = models.CharField('Рубрика', max_length=80, default='НОВОСТЬ')
    summary = models.CharField('Краткий анонс', max_length=400)
    body = models.TextField('Текст публикации', max_length=12000)
    published_on = models.DateField('Дата публикации', default=timezone.localdate)
    is_featured = models.BooleanField('Выделить первой', default=False)
    is_published = models.BooleanField('Опубликовано', default=False)
    public_checked = models.BooleanField('Проверено для публикации', default=False, editable=False)
    published_at = models.DateTimeField('Подтверждено к публикации', blank=True, null=True, editable=False)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Подтвердил', on_delete=models.PROTECT,
        blank=True, null=True, editable=False, related_name='published_public_news',
    )
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        verbose_name = 'Новость сайта'
        verbose_name_plural = 'Новости сайта'
        ordering = ['-is_featured', '-published_on', '-id']

    def __str__(self):
        return self.title


class PublicDocument(models.Model):
    category = models.ForeignKey(
        PublicDocumentCategory, verbose_name='Категория', on_delete=models.PROTECT, related_name='documents',
    )
    title = models.CharField('Название', max_length=200)
    description = models.CharField('Краткое описание', max_length=500, blank=True)
    document = models.FileField('Файл', upload_to=public_document_path, max_length=300)
    original_name = models.CharField('Исходное имя файла', max_length=255, editable=False)
    file_size = models.PositiveBigIntegerField('Размер, байт', editable=False)
    document_date = models.DateField('Дата документа', default=timezone.localdate)
    is_published = models.BooleanField('Опубликовано', default=False)
    public_checked = models.BooleanField('Проверено для публикации', default=False, editable=False)
    published_at = models.DateTimeField('Подтверждено к публикации', blank=True, null=True, editable=False)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name='Подтвердил', on_delete=models.PROTECT,
        blank=True, null=True, editable=False, related_name='published_public_documents',
    )
    notes = models.TextField('Служебное примечание', blank=True)
    created_at = models.DateTimeField('Создано', auto_now_add=True)
    updated_at = models.DateTimeField('Изменено', auto_now=True)

    class Meta:
        verbose_name = 'Публичный документ'
        verbose_name_plural = 'Публичные документы'
        ordering = ['-document_date', '-id']

    def clean(self):
        if self.document:
            if getattr(self.document, 'size', 0) > 20 * 1024 * 1024:
                raise ValidationError({'document': 'Размер файла не должен превышать 20 МБ.'})
            suffix = Path(self.document.name).suffix.lower()
            if suffix not in PUBLIC_DOCUMENT_EXTENSIONS:
                allowed = ', '.join(sorted(PUBLIC_DOCUMENT_EXTENSIONS))
                raise ValidationError({'document': f'Недопустимый тип файла. Разрешены: {allowed}.'})
        if self._state.adding and self.category_id and not self.category.active:
            raise ValidationError({'category': 'Эту категорию больше нельзя выбирать.'})

    def save(self, *args, **kwargs):
        if self.document and (self._state.adding or not self.original_name):
            self.original_name = Path(self.document.name).name[:255]
            self.file_size = self.document.size
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.title
