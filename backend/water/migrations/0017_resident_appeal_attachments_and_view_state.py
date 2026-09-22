from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
import water.resident_models


class Migration(migrations.Migration):
    dependencies = [
        ('water', '0016_alter_controllerreadingsubmission_reading'),
    ]

    operations = [
        migrations.CreateModel(
            name='ResidentAppealAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('document', models.FileField(max_length=300, upload_to=water.resident_models.appeal_attachment_path, verbose_name='Файл')),
                ('original_name', models.CharField(editable=False, max_length=255, verbose_name='Исходное имя')),
                ('file_size', models.PositiveBigIntegerField(editable=False, verbose_name='Размер, байт')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Загружено')),
                ('appeal', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='attachments', to='water.residentappeal', verbose_name='Обращение')),
                ('message', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='attachments', to='water.residentappealmessage', verbose_name='Сообщение жителя')),
                ('uploaded_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='resident_appeal_attachments', to=settings.AUTH_USER_MODEL, verbose_name='Загрузил')),
            ],
            options={
                'verbose_name': 'Вложение обращения',
                'verbose_name_plural': 'Вложения обращений',
                'ordering': ['created_at', 'id'],
            },
        ),
        migrations.CreateModel(
            name='ResidentAppealViewState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('last_seen_response_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('appeal', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='view_states', to='water.residentappeal')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='appeal_view_states', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='residentappealviewstate',
            constraint=models.UniqueConstraint(fields=('user', 'appeal'), name='resident_appeal_view_state_unique'),
        ),
    ]
