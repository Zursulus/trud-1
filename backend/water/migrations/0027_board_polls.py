# Generated for ZUR-53 on 2026-09-24

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import water.board_polls


class Migration(migrations.Migration):

    dependencies = [
        ('water', '0026_controller_line_access'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BoardAuditEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('target_type', models.CharField(max_length=40)),
                ('target_id', models.PositiveBigIntegerField()),
                ('action', models.CharField(choices=[('created', 'Создано'), ('changed', 'Изменено')], max_length=12)),
                ('summary', models.CharField(blank=True, max_length=200)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='board_audit_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Аудит предварительного опроса',
                'verbose_name_plural': 'Аудит предварительных опросов',
                'ordering': ['-created_at', '-id'],
                'indexes': [models.Index(fields=['target_type', 'target_id', '-created_at'], name='water_board_target__357fc3_idx')],
            },
        ),
        migrations.CreateModel(
            name='BoardMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('role', models.CharField(choices=[('chair', 'Председатель'), ('member', 'Член правления')], default='member', max_length=20)),
                ('starts', models.DateField(verbose_name='В правлении с')),
                ('ends', models.DateField(blank=True, null=True, verbose_name='В правлении до (не включая)')),
                ('basis', models.CharField(blank=True, max_length=300, verbose_name='Основание')),
                ('notes', models.TextField(blank=True, verbose_name='Примечание')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='board_memberships', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Член правления',
                'verbose_name_plural': 'Члены правления',
                'ordering': ['user', '-starts', 'id'],
                'constraints': [models.CheckConstraint(condition=models.Q(('ends__isnull', True), ('ends__gt', models.F('starts')), _connector='OR'), name='board_membership_dates')],
            },
        ),
        migrations.CreateModel(
            name='BoardPoll',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=200, verbose_name='Название')),
                ('description', models.TextField(blank=True, verbose_name='Пояснение')),
                ('opens_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Начало')),
                ('closes_at', models.DateTimeField(verbose_name='Срок ответа')),
                ('closed_at', models.DateTimeField(blank=True, null=True, verbose_name='Закрыто вручную')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='created_board_polls', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Предварительный опрос правления',
                'verbose_name_plural': 'Предварительные опросы правления',
                'ordering': ['-opens_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='BoardQuestion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('order', models.PositiveSmallIntegerField(default=1, verbose_name='Порядок')),
                ('text', models.CharField(max_length=1000, verbose_name='Вопрос')),
                ('poll', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='questions', to='water.boardpoll')),
            ],
            options={
                'verbose_name': 'Вопрос предварительного опроса',
                'verbose_name_plural': 'Вопросы предварительных опросов',
                'ordering': ['poll', 'order', 'id'],
                'constraints': [models.UniqueConstraint(fields=('poll', 'order'), name='board_question_order_unique')],
            },
        ),
        migrations.CreateModel(
            name='BoardDiscussionComment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('body', models.TextField(max_length=3000, verbose_name='Комментарий')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('author', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='board_discussion_comments', to=settings.AUTH_USER_MODEL)),
                ('question', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='discussion_comments', to='water.boardquestion')),
            ],
            options={
                'verbose_name': 'Комментарий правления',
                'verbose_name_plural': 'Комментарии правления',
                'ordering': ['created_at', 'id'],
            },
        ),
        migrations.CreateModel(
            name='BoardProtocol',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('document', models.FileField(max_length=300, upload_to=water.board_polls.board_protocol_path, verbose_name='Официальный протокол')),
                ('original_name', models.CharField(editable=False, max_length=255)),
                ('file_size', models.PositiveBigIntegerField(editable=False)),
                ('uploaded_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('poll', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='protocol', to='water.boardpoll')),
                ('uploaded_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='uploaded_board_protocols', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Официальный протокол после опроса',
                'verbose_name_plural': 'Официальные протоколы после опросов',
            },
        ),
        migrations.CreateModel(
            name='BoardVote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('choice', models.CharField(choices=[('for', 'За'), ('against', 'Против'), ('abstain', 'Воздержался')], max_length=12)),
                ('comment', models.TextField(blank=True, max_length=2000, verbose_name='Комментарий к голосу')),
                ('cast_at', models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ('question', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='votes', to='water.boardquestion')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='board_votes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Голос правления',
                'verbose_name_plural': 'Голоса правления',
                'ordering': ['question', 'user'],
                'constraints': [models.UniqueConstraint(fields=('question', 'user'), name='one_board_vote_per_question')],
            },
        ),
    ]
