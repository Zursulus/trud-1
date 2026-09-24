# Generated for ZUR-64 on 2026-09-24

import django.db.models.deletion
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('water', '0025_charge_obligation_person_plot_context'),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalControllerLineAccess',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('starts', models.DateField(verbose_name='Доступ с')),
                ('ends', models.DateField(blank=True, null=True, verbose_name='Доступ до (не включая)')),
                ('notes', models.TextField(blank=True, verbose_name='Примечание')),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('group', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='water.watergroup', verbose_name='Линия / группа')),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('user', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Старший / контролёр')),
            ],
            options={
                'verbose_name': 'historical Доступ старшего к линии',
                'verbose_name_plural': 'historical Доступ старших к линиям',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='ControllerLineAccess',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('starts', models.DateField(verbose_name='Доступ с')),
                ('ends', models.DateField(blank=True, null=True, verbose_name='Доступ до (не включая)')),
                ('notes', models.TextField(blank=True, verbose_name='Примечание')),
                ('group', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='controller_accesses', to='water.watergroup', verbose_name='Линия / группа')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='water_line_accesses', to=settings.AUTH_USER_MODEL, verbose_name='Старший / контролёр')),
            ],
            options={
                'verbose_name': 'Доступ старшего к линии',
                'verbose_name_plural': 'Доступ старших к линиям',
                'ordering': ['group', '-starts', 'id'],
                'permissions': [('use_controller_workspace', 'Может вносить показания закреплённых линий')],
                'constraints': [
                    models.CheckConstraint(
                        condition=models.Q(('ends__isnull', True), ('ends__gt', models.F('starts')), _connector='OR'),
                        name='controller_line_access_dates',
                    ),
                ],
            },
        ),
    ]
