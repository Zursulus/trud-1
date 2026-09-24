from pathlib import Path
import uuid

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from .models import User


BOARD_PROTOCOL_EXTENSIONS = {'.pdf', '.docx'}
BOARD_PROTOCOL_MAX_BYTES = 15 * 1024 * 1024


class BoardAuditEvent(models.Model):
    """Immutable audit event for the non-official board polling contour."""

    target_type = models.CharField(max_length=40)
    target_id = models.PositiveBigIntegerField()
    action = models.CharField(max_length=12, choices=[('created', 'Создано'), ('changed', 'Изменено')])
    actor = models.ForeignKey(
        User, on_delete=models.PROTECT, blank=True, null=True,
        related_name='board_audit_events',
    )
    summary = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        verbose_name = 'Аудит предварительного опроса'
        verbose_name_plural = 'Аудит предварительных опросов'
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['target_type', 'target_id', '-created_at'])]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Событие аудита нельзя изменять.')
        self.full_clean()
        return super().save(*args, **kwargs)


class BoardRecordedModel(models.Model):
    """Optimistic-lock record with an immutable audit event on every write."""

    version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        adding = self._state.adding
        with transaction.atomic():
            if not adding:
                stored = type(self).objects.select_for_update().get(pk=self.pk)
                if stored.version != self.version:
                    raise ValidationError('Запись уже изменена. Обновите страницу и повторите ввод.')
            self.full_clean()
            self.version += 1
            if kwargs.get('update_fields'):
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'version', 'updated_at'}
            result = super().save(*args, **kwargs)
            BoardAuditEvent.objects.create(
                target_type=self._meta.model_name,
                target_id=self.pk,
                action='created' if adding else 'changed',
                actor=getattr(self, '_audit_actor', None),
                summary=str(getattr(self, '_audit_reason', ''))[:200],
            )
            return result


class BoardMembership(BoardRecordedModel):
    ROLE_CHAIR = 'chair'
    ROLE_MEMBER = 'member'
    ROLE_CHOICES = [(ROLE_CHAIR, 'Председатель'), (ROLE_MEMBER, 'Член правления')]

    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name='board_memberships')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    starts = models.DateField('В правлении с')
    ends = models.DateField('В правлении до (не включая)', blank=True, null=True)
    basis = models.CharField('Основание', max_length=300, blank=True)
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Член правления'
        verbose_name_plural = 'Члены правления'
        ordering = ['user', '-starts', 'id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends__isnull=True) | models.Q(ends__gt=models.F('starts')),
                name='board_membership_dates',
            ),
        ]

    def clean(self):
        if not self.user_id or not self.starts:
            return
        others = BoardMembership.objects.filter(user_id=self.user_id).exclude(pk=self.pk)
        others = others.filter(models.Q(ends__isnull=True) | models.Q(ends__gt=self.starts))
        if self.ends:
            others = others.filter(starts__lt=self.ends)
        if others.exists():
            raise ValidationError('У пользователя уже есть пересекающийся срок в правлении.')

    def __str__(self):
        return f'{self.user} · {self.get_role_display()}'


class BoardPoll(BoardRecordedModel):
    title = models.CharField('Название', max_length=200)
    description = models.TextField('Пояснение', blank=True)
    opens_at = models.DateTimeField('Начало', default=timezone.now)
    closes_at = models.DateTimeField('Срок ответа')
    closed_at = models.DateTimeField('Закрыто вручную', blank=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='created_board_polls')

    class Meta:
        verbose_name = 'Предварительный опрос правления'
        verbose_name_plural = 'Предварительные опросы правления'
        ordering = ['-opens_at', '-id']

    def clean(self):
        if self.closes_at and self.opens_at and self.closes_at <= self.opens_at:
            raise ValidationError({'closes_at': 'Срок ответа должен быть позже начала опроса.'})
        if self.closed_at and self.opens_at and self.closed_at < self.opens_at:
            raise ValidationError({'closed_at': 'Опрос нельзя закрыть раньше его начала.'})
        if self.created_by_id and not self.created_by.is_staff:
            raise ValidationError({'created_by': 'Создать опрос может только сотрудник.'})

    @property
    def is_open(self):
        now = timezone.now()
        return self.closed_at is None and self.opens_at <= now < self.closes_at

    def __str__(self):
        return self.title


class BoardQuestion(BoardRecordedModel):
    poll = models.ForeignKey(BoardPoll, on_delete=models.PROTECT, related_name='questions')
    order = models.PositiveSmallIntegerField('Порядок', default=1)
    text = models.CharField('Вопрос', max_length=1000)

    class Meta:
        verbose_name = 'Вопрос предварительного опроса'
        verbose_name_plural = 'Вопросы предварительных опросов'
        ordering = ['poll', 'order', 'id']
        constraints = [models.UniqueConstraint(fields=['poll', 'order'], name='board_question_order_unique')]

    def clean(self):
        if self.pk and BoardVote.objects.filter(question_id=self.pk).exists():
            stored = BoardQuestion.objects.get(pk=self.pk)
            if (stored.poll_id, stored.order, stored.text) != (self.poll_id, self.order, self.text):
                raise ValidationError('Вопрос с голосами нельзя переписывать.')

    def __str__(self):
        return self.text


class BoardVote(BoardRecordedModel):
    CHOICE_FOR = 'for'
    CHOICE_AGAINST = 'against'
    CHOICE_ABSTAIN = 'abstain'
    CHOICES = [
        (CHOICE_FOR, 'За'),
        (CHOICE_AGAINST, 'Против'),
        (CHOICE_ABSTAIN, 'Воздержался'),
    ]

    question = models.ForeignKey(BoardQuestion, on_delete=models.PROTECT, related_name='votes')
    user = models.ForeignKey(User, on_delete=models.PROTECT, related_name='board_votes')
    choice = models.CharField(max_length=12, choices=CHOICES)
    comment = models.TextField('Комментарий к голосу', max_length=2000, blank=True)
    cast_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        verbose_name = 'Голос правления'
        verbose_name_plural = 'Голоса правления'
        ordering = ['question', 'user']
        constraints = [models.UniqueConstraint(fields=['question', 'user'], name='one_board_vote_per_question')]

    def clean(self):
        if not self.question_id or not self.user_id:
            return
        if not self.question.poll.is_open:
            raise ValidationError('Опрос уже закрыт или ещё не начался.')
        if active_board_membership(self.user) is None:
            raise ValidationError('Голосовать может только действующий член правления.')

    def __str__(self):
        return f'{self.question_id} · {self.user} · {self.get_choice_display()}'


class BoardDiscussionComment(models.Model):
    question = models.ForeignKey(BoardQuestion, on_delete=models.PROTECT, related_name='discussion_comments')
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name='board_discussion_comments')
    body = models.TextField('Комментарий', max_length=3000)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        verbose_name = 'Комментарий правления'
        verbose_name_plural = 'Комментарии правления'
        ordering = ['created_at', 'id']

    def clean(self):
        if self.author_id and active_board_membership(self.author) is None:
            raise ValidationError({'author': 'Комментировать может только действующий член правления.'})
        if self.question_id and not self.question.poll.is_open:
            raise ValidationError('Комментарий можно добавить только в открытый опрос.')
        if self.pk:
            stored = type(self).objects.get(pk=self.pk)
            if (stored.question_id, stored.author_id, stored.body) != (self.question_id, self.author_id, self.body):
                raise ValidationError('Отправленный комментарий нельзя переписывать.')

    def save(self, *args, **kwargs):
        adding = self._state.adding
        self.full_clean()
        result = super().save(*args, **kwargs)
        if adding:
            BoardAuditEvent.objects.create(
                target_type='boarddiscussioncomment', target_id=self.pk, action='created',
                actor=self.author, summary='Комментарий к вопросу',
            )
        return result


def board_protocol_path(instance, filename):
    suffix = Path(filename).suffix.lower()[:12]
    return f'board-protocols/{timezone.localdate():%Y/%m}/{uuid.uuid4().hex}{suffix}'


class BoardProtocol(models.Model):
    poll = models.OneToOneField(BoardPoll, on_delete=models.PROTECT, related_name='protocol')
    document = models.FileField('Официальный протокол', upload_to=board_protocol_path, max_length=300)
    original_name = models.CharField(max_length=255, editable=False)
    file_size = models.PositiveBigIntegerField(editable=False)
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name='uploaded_board_protocols')
    uploaded_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        verbose_name = 'Официальный протокол после опроса'
        verbose_name_plural = 'Официальные протоколы после опросов'

    def clean(self):
        if self.uploaded_by_id and not self.uploaded_by.is_staff:
            raise ValidationError({'uploaded_by': 'Протокол может загрузить только сотрудник.'})
        if self.poll_id and self.poll.is_open:
            raise ValidationError({'poll': 'Официальный протокол связывают после завершения предварительного опроса.'})
        if self.document:
            if getattr(self.document, 'size', 0) > BOARD_PROTOCOL_MAX_BYTES:
                raise ValidationError({'document': 'Файл должен быть не больше 15 МБ.'})
            if Path(self.document.name).suffix.lower() not in BOARD_PROTOCOL_EXTENSIONS:
                raise ValidationError({'document': 'Разрешены только PDF и DOCX.'})
        if self.pk:
            stored = type(self).objects.get(pk=self.pk)
            if stored.poll_id != self.poll_id or stored.document.name != self.document.name:
                raise ValidationError('Сохранённый протокол нельзя подменить.')

    def save(self, *args, **kwargs):
        adding = self._state.adding
        if self.document and (adding or not self.original_name):
            self.original_name = Path(self.document.name).name[:255]
            self.file_size = self.document.size
        self.full_clean()
        result = super().save(*args, **kwargs)
        if adding:
            BoardAuditEvent.objects.create(
                target_type='boardprotocol', target_id=self.pk, action='created',
                actor=self.uploaded_by, summary='Связан официальный протокол',
            )
        return result


def active_board_membership(user, on=None):
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_active', False):
        return None
    on = on or timezone.localdate()
    return BoardMembership.objects.filter(user=user, starts__lte=on).filter(
        models.Q(ends__isnull=True) | models.Q(ends__gt=on),
    ).order_by('-starts', '-id').first()


def pending_board_poll_count(user):
    if active_board_membership(user) is None:
        return 0
    now = timezone.now()
    open_polls = BoardPoll.objects.filter(
        opens_at__lte=now, closes_at__gt=now, closed_at__isnull=True,
    )
    pending = 0
    for poll in open_polls.prefetch_related('questions'):
        question_ids = [question.pk for question in poll.questions.all()]
        if question_ids and BoardVote.objects.filter(question_id__in=question_ids, user=user).count() < len(question_ids):
            pending += 1
    return pending
