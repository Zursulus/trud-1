[Reading 43 lines from start (total: 43 lines, 0 remaining)]

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('water', '0028_observation_sources_and_line_review'),
    ]

    operations = [
        migrations.CreateModel(
            name='SecurityAlert',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('malware', 'Вредоносный файл'), ('suspicious_file', 'Подозрительный файл'), ('scanner', 'Антивирус недоступен'), ('spam', 'Подозрение на спам'), ('upload_quota', 'Превышение лимита вложений')], max_length=32, verbose_name='Тип')),
                ('severity', models.CharField(choices=[('warning', 'Требует проверки'), ('critical', 'Критично')], default='warning', max_length=12, verbose_name='Уровень')),
                ('original_name', models.CharField(blank=True, max_length=255, verbose_name='Имя файла')),
                ('file_size', models.PositiveBigIntegerField(blank=True, null=True, verbose_name='Размер файла')),
                ('source_hash', models.CharField(blank=True, max_length=64, verbose_name='Хэш сетевого источника')),
                ('detail', models.CharField(blank=True, max_length=300, verbose_name='Техническая причина')),
                ('fingerprint', models.CharField(editable=False, max_length=64, unique=True, verbose_name='Дедупликация')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Обнаружено')),
                ('resolved_at', models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Проверено')),
                ('account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='security_alerts', to='water.account', verbose_name='Лицевой счёт')),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='security_alerts', to=settings.AUTH_USER_MODEL, verbose_name='Пользователь')),
                ('appeal', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='security_alerts', to='water.residentappeal', verbose_name='Обращение')),
                ('resolved_by', models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='resolved_security_alerts', to=settings.AUTH_USER_MODEL, verbose_name='Проверил')),
            ],
            options={
                'verbose_name': 'Сигнал безопасности',
                'verbose_name_plural': 'Сигналы безопасности',
                'ordering': ['resolved_at', '-created_at', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='securityalert',
            index=models.Index(fields=['resolved_at', 'created_at'], name='water_sec_alert_state_idx'),
        ),
    ]
