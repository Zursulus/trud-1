# Generated for ZUR-68 on 2026-09-24

import django.db.models.deletion
import django.utils.timezone
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('water', '0020_member_registry_without_fio'),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalResidentIdentity',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('verified_at', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Личность подтверждена')),
                ('basis', models.CharField(blank=True, max_length=300, verbose_name='Основание подтверждения')),
                ('notes', models.TextField(blank=True, verbose_name='Примечание')),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('person', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='water.person', verbose_name='Человек')),
                ('user', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Кабинет')),
                ('verified_by', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to=settings.AUTH_USER_MODEL, verbose_name='Кто подтвердил')),
            ],
            options={
                'verbose_name': 'historical Идентичность кабинета жителя',
                'verbose_name_plural': 'historical Идентичности кабинетов жителей',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='HistoricalTsnMembership',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('application_on', models.DateField(verbose_name='Дата заявления')),
                ('starts', models.DateField(verbose_name='Членство с')),
                ('decision_ref', models.CharField(max_length=300, verbose_name='Решение правления / основание приёма')),
                ('member_document', models.CharField(blank=True, max_length=300, verbose_name='Документ о членстве')),
                ('ends', models.DateField(blank=True, null=True, verbose_name='Членство прекращено (дата)')),
                ('end_reason', models.CharField(blank=True, choices=[('voluntary', 'Добровольный выход'), ('right_ended', 'Прекращение права на участок'), ('death', 'Смерть'), ('exclusion', 'Исключение')], max_length=20, verbose_name='Основание прекращения')),
                ('end_document', models.CharField(blank=True, max_length=300, verbose_name='Документ о прекращении')),
                ('notes', models.TextField(blank=True, verbose_name='Примечание')),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('person', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='water.person', verbose_name='Член ТСН')),
            ],
            options={
                'verbose_name': 'historical Членство в ТСН',
                'verbose_name_plural': 'historical Членство в ТСН',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='ResidentIdentity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('verified_at', models.DateTimeField(default=django.utils.timezone.now, editable=False, verbose_name='Личность подтверждена')),
                ('basis', models.CharField(blank=True, max_length=300, verbose_name='Основание подтверждения')),
                ('notes', models.TextField(blank=True, verbose_name='Примечание')),
                ('person', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='resident_identity', to='water.person', verbose_name='Человек')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='resident_identity', to=settings.AUTH_USER_MODEL, verbose_name='Кабинет')),
                ('verified_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='verified_resident_identities', to=settings.AUTH_USER_MODEL, verbose_name='Кто подтвердил')),
            ],
            options={
                'verbose_name': 'Идентичность кабинета жителя',
                'verbose_name_plural': 'Идентичности кабинетов жителей',
                'ordering': ['person', 'id'],
            },
        ),
        migrations.CreateModel(
            name='TsnMembership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('application_on', models.DateField(verbose_name='Дата заявления')),
                ('starts', models.DateField(verbose_name='Членство с')),
                ('decision_ref', models.CharField(max_length=300, verbose_name='Решение правления / основание приёма')),
                ('member_document', models.CharField(blank=True, max_length=300, verbose_name='Документ о членстве')),
                ('ends', models.DateField(blank=True, null=True, verbose_name='Членство прекращено (дата)')),
                ('end_reason', models.CharField(blank=True, choices=[('voluntary', 'Добровольный выход'), ('right_ended', 'Прекращение права на участок'), ('death', 'Смерть'), ('exclusion', 'Исключение')], max_length=20, verbose_name='Основание прекращения')),
                ('end_document', models.CharField(blank=True, max_length=300, verbose_name='Документ о прекращении')),
                ('notes', models.TextField(blank=True, verbose_name='Примечание')),
                ('person', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='tsn_memberships', to='water.person', verbose_name='Член ТСН')),
            ],
            options={
                'verbose_name': 'Членство в ТСН',
                'verbose_name_plural': 'Членство в ТСН',
                'ordering': ['person', '-starts', 'id'],
                'constraints': [
                    models.CheckConstraint(condition=models.Q(('ends__isnull', True), ('ends__gt', models.F('starts')), _connector='OR'), name='tsn_membership_dates'),
                    models.UniqueConstraint(condition=models.Q(('ends__isnull', True)), fields=('person',), name='one_active_tsn_membership_per_person'),
                ],
            },
        ),
    ]
