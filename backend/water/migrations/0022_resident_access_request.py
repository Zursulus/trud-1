# Generated for ZUR-69 on 2026-09-24

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('water', '0021_resident_identity_tsn_membership'),
    ]

    operations = [
        migrations.CreateModel(
            name='ResidentAccessRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('full_name', models.CharField(max_length=200, verbose_name='Как к вам обращаться')),
                ('email', models.EmailField(max_length=254, verbose_name='Электронная почта')),
                ('phone', models.CharField(blank=True, max_length=40, verbose_name='Телефон')),
                ('plot_hint', models.CharField(max_length=200, verbose_name='Участок / адрес')),
                ('claimed_role', models.CharField(choices=[('owner', 'Собственник'), ('representative', 'Представитель'), ('payer', 'Плательщик'), ('other', 'Другое основание')], max_length=20, verbose_name='Основание запроса')),
                ('message', models.TextField(blank=True, max_length=1000, verbose_name='Комментарий')),
                ('submitted_at', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Получено')),
                ('submission_key', models.CharField(db_index=True, editable=False, max_length=64)),
                ('status', models.CharField(choices=[('new', 'Новая'), ('approved', 'Одобрена'), ('rejected', 'Отклонена')], default='new', editable=False, max_length=20, verbose_name='Решение')),
                ('approved_role', models.CharField(blank=True, choices=[('owner', 'Собственник'), ('representative', 'Представитель'), ('payer', 'Плательщик')], editable=False, max_length=20, verbose_name='Одобренное основание доступа')),
                ('decision_note', models.TextField(blank=True, editable=False, max_length=1000, verbose_name='Служебное основание решения')),
                ('decided_at', models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Решено')),
                ('decided_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='decided_resident_access_requests', to=settings.AUTH_USER_MODEL, verbose_name='Решил')),
                ('invite', models.OneToOneField(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='access_request', to='water.residentinvite', verbose_name='Созданное приглашение')),
                ('matched_account', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='resident_access_requests', to='water.account', verbose_name='Проверенный лицевой счёт')),
            ],
            options={
                'verbose_name': 'Запрос доступа к кабинету',
                'verbose_name_plural': 'Запросы доступа к кабинету',
                'ordering': ['status', '-submitted_at', '-id'],
            },
        ),
    ]
