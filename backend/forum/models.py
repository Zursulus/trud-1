from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.utils import timezone

from water.models import RecordedModel


class Section(models.Model):
    name = models.CharField('Название', max_length=100)
    description = models.CharField('Описание', max_length=300, blank=True)

    class Meta:
        ordering = ['id']
        verbose_name = 'Раздел форума'
        verbose_name_plural = 'Разделы форума'

    def __str__(self):
        return self.name


class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='forum_profile')
    nickname = models.CharField('Ник', max_length=40, blank=True)

    def __str__(self):
        return self.nickname or f'Житель {self.user_id}'


class Authored(RecordedModel):
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    body = models.TextField('Текст', validators=[MaxLengthValidator(5000)])
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    edited_at = models.DateTimeField(null=True, editable=False)
    hidden = models.BooleanField('Скрыто', default=False)
    hidden_reason = models.CharField('Причина скрытия', max_length=300, blank=True)

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        if self.hidden and not self.hidden_reason.strip():
            raise ValidationError({'hidden_reason': 'Укажите причину скрытия.'})
        if not self.body.strip():
            raise ValidationError({'body': 'Введите текст.'})

    def editable_by(self, user):
        topic = self if isinstance(self, Topic) else self.topic
        return (
            user.is_authenticated and user.pk == self.author_id
            and timezone.now() < self.created_at + timedelta(minutes=15)
            and not self.hidden and not topic.hidden and not topic.closed
        )

    @property
    def display_name(self):
        # Never expose username, email, full name or plot from the existing registry.
        profile = getattr(self.author, 'forum_profile', None)
        return str(profile) if profile else f'Житель {self.author_id}'


class Topic(Authored):
    section = models.ForeignKey(Section, on_delete=models.PROTECT, related_name='topics')
    title = models.CharField('Тема', max_length=160)
    pinned = models.BooleanField('Закреплена', default=False)
    closed = models.BooleanField('Закрыта', default=False)

    class Meta:
        ordering = ['-pinned', '-created_at', '-id']
        permissions = [('moderate_forum', 'Может модерировать форум')]
        verbose_name = 'Тема форума'
        verbose_name_plural = 'Темы форума'

    def __str__(self):
        return self.title


class Post(Authored):
    topic = models.ForeignKey(Topic, on_delete=models.PROTECT, related_name='posts')

    class Meta:
        ordering = ['created_at', 'id']
        verbose_name = 'Ответ форума'
        verbose_name_plural = 'Ответы форума'


class Report(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.PROTECT)
    post = models.ForeignKey(Post, null=True, blank=True, on_delete=models.PROTECT)
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.CharField('Причина жалобы', max_length=300)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    resolved_at = models.DateTimeField(null=True, editable=False)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name='+', editable=False,
    )

    class Meta:
        ordering = ['-created_at', '-id']
        verbose_name = 'Жалоба форума'
        verbose_name_plural = 'Жалобы форума'

    def clean(self):
        if self.post_id and self.post.topic_id != self.topic_id:
            raise ValidationError('Ответ не принадлежит теме.')
