from pathlib import Path
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.utils import timezone

from water.models import RecordedModel


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
    is_featured = models.BooleanField('Закреплено', default=False)
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

    @property
    def site_status(self):
        if not self.is_published or not self.public_checked:
            return 'Черновик'
        return 'Запланировано' if self.published_on > timezone.localdate() else 'На сайте'

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

    @property
    def site_status(self):
        if not self.is_published or not self.public_checked:
            return 'Черновик'
        return 'Запланировано' if self.document_date > timezone.localdate() else 'На сайте'

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


class PublicImage(RecordedModel):
    title = models.CharField('Название картинки', max_length=180)
    image = models.FileField('Картинка (PNG или JPEG, до 5 МБ)', upload_to=public_document_path, max_length=300)

    class Meta:
        verbose_name = 'Картинка сайта'
        verbose_name_plural = 'Картинки сайта'

    def clean(self):
        super().clean()
        if self.image:
            if self.image.size > 5 * 1024 * 1024:
                raise ValidationError({'image': 'Картинка должна быть не больше 5 МБ.'})
            self.image.open('rb')
            header = self.image.read(16)
            self.image.seek(0)
            suffix = Path(self.image.name).suffix.lower()
            if not ((suffix == '.png' and header.startswith(b'\x89PNG\r\n\x1a\n')) or
                    (suffix in {'.jpg', '.jpeg'} and header.startswith(b'\xff\xd8\xff'))):
                raise ValidationError({'image': 'Выберите картинку PNG или JPEG.'})
        if self.pk and self.image.name != type(self).objects.get(pk=self.pk).image.name:
            raise ValidationError({'image': 'Загруженный файл не заменяется; создайте новую картинку.'})

    def __str__(self):
        return self.title


class HomepagePublication(RecordedModel):
    is_published = models.BooleanField('Опубликовано', default=False)
    public_checked = models.BooleanField(default=False, editable=False)
    published_at = models.DateTimeField(null=True, blank=True, editable=False)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     null=True, blank=True, editable=False, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    @property
    def site_status(self):
        if not self.is_published or not self.public_checked:
            return 'Черновик'
        today = timezone.localdate()
        if getattr(self, 'starts_on', None) and self.starts_on > today:
            return 'Запланировано'
        if getattr(self, 'ends_on', None) and self.ends_on < today:
            return 'Показ завершён'
        return 'На сайте'


class HomepageAnnouncement(HomepagePublication):
    title = models.CharField('Заголовок', max_length=180)
    subtitle = models.CharField('Подзаголовок', max_length=400, blank=True)
    body = models.TextField('Текст', max_length=12000, blank=True)
    label = models.CharField('Метка', max_length=100, default='Важно для членов ТСН')
    button_text = models.CharField('Текст кнопки', max_length=100, blank=True)
    button_url = models.URLField('Внешняя ссылка', blank=True, max_length=500, validators=[URLValidator(schemes=['http', 'https'])])
    document = models.ForeignKey(PublicDocument, verbose_name='Документ', null=True, blank=True, on_delete=models.PROTECT)
    news = models.ForeignKey(PublicNews, verbose_name='Новость', null=True, blank=True, on_delete=models.PROTECT)
    image = models.ForeignKey(PublicImage, verbose_name='Картинка', null=True, blank=True, on_delete=models.PROTECT)
    starts_on = models.DateField('Показывать с', default=timezone.localdate)
    ends_on = models.DateField('Показывать по (включительно)', null=True, blank=True)

    class Meta:
        verbose_name = 'Главное объявление'
        verbose_name_plural = 'Главные объявления'
        ordering = ['-starts_on', '-pk']

    @property
    def site_status(self):
        status = super().site_status
        if status == 'На сайте':
            from .homepage import active_announcement
            active_id = getattr(self, '_active_announcement_id', None)
            if not hasattr(self, '_active_announcement_id'):
                active = active_announcement()
                active_id = active.pk if active else None
            if active_id != self.pk:
                return 'Заменено другим объявлением'
        return status

    def clean(self):
        super().clean()
        if self.ends_on and self.starts_on and self.ends_on < self.starts_on:
            raise ValidationError({'ends_on': 'Конец показа не может быть раньше начала.'})
        targets = sum(bool(value) for value in (self.button_url, self.document_id, self.news_id))
        if targets > 1 or bool(self.button_text) != bool(targets):
            raise ValidationError('Для кнопки укажите текст и одну ссылку: документ, новость или внешний адрес.')
        if self.is_published:
            for field in ('document', 'news'):
                target = getattr(self, field)
                if target and not (target.is_published and target.public_checked):
                    raise ValidationError({field: 'Сначала опубликуйте выбранный материал.'})

    def __str__(self):
        return self.title


class SiteContacts(HomepagePublication):
    singleton = models.PositiveSmallIntegerField(default=1, unique=True, editable=False)
    email = models.EmailField('Почта', blank=True)
    phone = models.CharField('Телефон', max_length=80, blank=True)
    address = models.CharField('Адрес', max_length=400, blank=True)
    office_hours = models.TextField('Часы приёма', max_length=2000, blank=True)
    how_to_contact = models.TextField('Как связаться', max_length=4000, blank=True)

    class Meta:
        verbose_name = 'Контакты и приём'
        verbose_name_plural = 'Контакты и приём'
        constraints = [models.CheckConstraint(condition=models.Q(singleton=1), name='site_contacts_singleton')]

    def __str__(self):
        return 'Контакты и приём'
