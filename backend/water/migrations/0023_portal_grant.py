# Generated for ZUR-70 on 2026-09-24

import django.db.models.deletion
import django.utils.timezone
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('water', '0022_resident_access_request'),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalPortalGrant',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('starts', models.DateField(verbose_name='Доступ с')),
                ('ends', models.DateField(blank=True, null=True, verbose_name='Доступ до (не включая)')),
                ('can_view_account', models.BooleanField(default=True, verbose_name='Видеть участок и базовые данные')),
                ('can_view_finance', models.BooleanField(default=False, verbose_name='Видеть начисления и оплаты')),
                ('can_submit_water', models.BooleanField(default=False, verbose_name='Передавать показания воды')),
                ('can_view_documents', models.BooleanField(default=False, verbose_name='Видеть документы лицевого счёта')),
                ('can_use_appeals', models.BooleanField(default=False, verbose_name='Создавать и читать свои обращения')),
                ('can_represent', models.BooleanField(default=False, verbose_name='Совершать представительские действия')),
                ('basis', models.CharField(max_length=300, verbose_name='Проверенное основание')),
                ('verified_at', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Подтверждено')),
                ('notes', models.TextField(blank=True, verbose_name='Примечание')),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('account', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='water.account', verbose_name='Лицевой счёт')),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('person', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='water.person', verbose_name='Человек')),
                ('verified_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Кто подтвердил')),
            ],
            options={
                'verbose_name': 'historical Явное право доступа к кабинету',
                'verbose_name_plural': 'historical Явные права доступа к кабинетам',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='PortalGrant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('starts', models.DateField(verbose_name='Доступ с')),
                ('ends', models.DateField(blank=True, null=True, verbose_name='Доступ до (не включая)')),
                ('can_view_account', models.BooleanField(default=True, verbose_name='Видеть участок и базовые данные')),
                ('can_view_finance', models.BooleanField(default=False, verbose_name='Видеть начисления и оплаты')),
                ('can_submit_water', models.BooleanField(default=False, verbose_name='Передавать показания воды')),
                ('can_view_documents', models.BooleanField(default=False, verbose_name='Видеть документы лицевого счёта')),
                ('can_use_appeals', models.BooleanField(default=False, verbose_name='Создавать и читать свои обращения')),
                ('can_represent', models.BooleanField(default=False, verbose_name='Совершать представительские действия')),
                ('basis', models.CharField(max_length=300, verbose_name='Проверенное основание')),
                ('verified_at', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Подтверждено')),
                ('notes', models.TextField(blank=True, verbose_name='Примечание')),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='portal_grants', to='water.account', verbose_name='Лицевой счёт')),
                ('person', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='portal_grants', to='water.person', verbose_name='Человек')),
                ('verified_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='verified_portal_grants', to=settings.AUTH_USER_MODEL, verbose_name='Кто подтвердил')),
            ],
            options={
                'verbose_name': 'Явное право доступа к кабинету',
                'verbose_name_plural': 'Явные права доступа к кабинетам',
                'ordering': ['person', 'account', '-starts', 'id'],
                'constraints': [
                    models.CheckConstraint(condition=models.Q(('ends__isnull', True), ('ends__gt', models.F('starts')), _connector='OR'), name='portal_grant_dates'),
                ],
            },
        ),
    ]
