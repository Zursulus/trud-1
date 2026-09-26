# Generated for ZUR-201.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('water', '0027_board_polls'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='controllerreadingsubmission',
            options={'ordering': ['status', '-submitted_at', '-id'], 'verbose_name': 'Наблюдение счётчика', 'verbose_name_plural': '07 · Премодерация и сверка показаний'},
        ),
        migrations.AlterModelOptions(
            name='historicalcontrollerreadingsubmission',
            options={'get_latest_by': ('history_date', 'history_id'), 'ordering': ('-history_date', '-history_id'), 'verbose_name': 'historical Наблюдение счётчика', 'verbose_name_plural': 'historical 07 · Премодерация и сверка показаний'},
        ),
        migrations.AddField(
            model_name='controllerreadingsubmission',
            name='source',
            field=models.CharField(choices=[('controller', 'Независимый контролёр'), ('line_senior', 'Старший линии'), ('resident', 'Житель')], default='controller', max_length=20, verbose_name='Источник'),
        ),
        migrations.AddField(
            model_name='controllerreadingsubmission',
            name='line_review_status',
            field=models.CharField(choices=[('not_required', 'Не требуется'), ('pending', 'Ожидает старшего линии'), ('confirmed', 'Подтверждено старшим линии'), ('flagged', 'Старший линии отметил расхождение')], default='not_required', editable=False, max_length=20, verbose_name='Проверка старшим линии'),
        ),
        migrations.AddField(
            model_name='controllerreadingsubmission',
            name='line_review_comment',
            field=models.CharField(blank=True, editable=False, max_length=500, verbose_name='Комментарий старшего линии'),
        ),
        migrations.AddField(
            model_name='controllerreadingsubmission',
            name='line_reviewed_at',
            field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Проверено старшим линии'),
        ),
        migrations.AddField(
            model_name='controllerreadingsubmission',
            name='line_reviewed_by',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='line_reviewed_controller_readings', to=settings.AUTH_USER_MODEL, verbose_name='Проверил старший линии'),
        ),
        migrations.AddField(
            model_name='historicalcontrollerreadingsubmission',
            name='source',
            field=models.CharField(choices=[('controller', 'Независимый контролёр'), ('line_senior', 'Старший линии'), ('resident', 'Житель')], default='controller', max_length=20, verbose_name='Источник'),
        ),
        migrations.AddField(
            model_name='historicalcontrollerreadingsubmission',
            name='line_review_status',
            field=models.CharField(choices=[('not_required', 'Не требуется'), ('pending', 'Ожидает старшего линии'), ('confirmed', 'Подтверждено старшим линии'), ('flagged', 'Старший линии отметил расхождение')], default='not_required', editable=False, max_length=20, verbose_name='Проверка старшим линии'),
        ),
        migrations.AddField(
            model_name='historicalcontrollerreadingsubmission',
            name='line_review_comment',
            field=models.CharField(blank=True, editable=False, max_length=500, verbose_name='Комментарий старшего линии'),
        ),
        migrations.AddField(
            model_name='historicalcontrollerreadingsubmission',
            name='line_reviewed_at',
            field=models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Проверено старшим линии'),
        ),
        migrations.AddField(
            model_name='historicalcontrollerreadingsubmission',
            name='line_reviewed_by',
            field=models.ForeignKey(blank=True, db_constraint=False, editable=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Проверил старший линии'),
        ),
        migrations.AlterField(
            model_name='controllerreadingsubmission',
            name='submitted_by',
            field=models.ForeignKey(editable=False, on_delete=django.db.models.deletion.PROTECT, related_name='controller_reading_submissions', to=settings.AUTH_USER_MODEL, verbose_name='Кто передал'),
        ),
        migrations.AlterField(
            model_name='historicalcontrollerreadingsubmission',
            name='submitted_by',
            field=models.ForeignKey(blank=True, db_constraint=False, editable=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Кто передал'),
        ),
    ]
