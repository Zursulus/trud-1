# Generated for ZUR-54 on 2026-09-24

from decimal import Decimal

import django.core.validators
import django.db.models.deletion
import simple_history.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('water', '0023_portal_grant'),
    ]

    operations = [
        migrations.CreateModel(
            name='HistoricalChargeObligation',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('category', models.CharField(choices=[('membership', 'Членский взнос'), ('target', 'Целевой взнос'), ('water', 'Вода'), ('other', 'Прочее')], max_length=20, verbose_name='Категория обязательства')),
                ('payer_scope', models.CharField(choices=[('account', 'Лицевой счёт'), ('plot', 'Участок'), ('person', 'Конкретное лицо'), ('membership', 'Член ТСН')], max_length=20, verbose_name='Кто обязан платить')),
                ('due_on', models.DateField(verbose_name='Срок оплаты')),
                ('base_amount', models.DecimalField(decimal_places=2, max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal('0.00'))], verbose_name='Сумма до льготы, ₽')),
                ('relief_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal('0.00'))], verbose_name='Льгота / уменьшение, ₽')),
                ('basis', models.CharField(max_length=300, verbose_name='Основание начисления')),
                ('relief_basis', models.CharField(blank=True, max_length=300, verbose_name='Основание льготы')),
                ('notes', models.TextField(blank=True, verbose_name='Примечание')),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('charge', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='water.charge', verbose_name='Начисление')),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('membership', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='water.tsnmembership', verbose_name='Членство-плательщик')),
                ('person', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='water.person', verbose_name='Лицо-плательщик')),
                ('plot', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='water.landplot', verbose_name='Участок-плательщик')),
            ],
            options={
                'verbose_name': 'historical Основание финансового обязательства',
                'verbose_name_plural': 'historical Основания финансовых обязательств',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='ChargeObligation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('version', models.PositiveIntegerField(default=0)),
                ('category', models.CharField(choices=[('membership', 'Членский взнос'), ('target', 'Целевой взнос'), ('water', 'Вода'), ('other', 'Прочее')], max_length=20, verbose_name='Категория обязательства')),
                ('payer_scope', models.CharField(choices=[('account', 'Лицевой счёт'), ('plot', 'Участок'), ('person', 'Конкретное лицо'), ('membership', 'Член ТСН')], max_length=20, verbose_name='Кто обязан платить')),
                ('due_on', models.DateField(verbose_name='Срок оплаты')),
                ('base_amount', models.DecimalField(decimal_places=2, max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal('0.00'))], verbose_name='Сумма до льготы, ₽')),
                ('relief_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=14, validators=[django.core.validators.MinValueValidator(Decimal('0.00'))], verbose_name='Льгота / уменьшение, ₽')),
                ('basis', models.CharField(max_length=300, verbose_name='Основание начисления')),
                ('relief_basis', models.CharField(blank=True, max_length=300, verbose_name='Основание льготы')),
                ('notes', models.TextField(blank=True, verbose_name='Примечание')),
                ('charge', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='obligation', to='water.charge', verbose_name='Начисление')),
                ('membership', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='charge_obligations', to='water.tsnmembership', verbose_name='Членство-плательщик')),
                ('person', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='charge_obligations', to='water.person', verbose_name='Лицо-плательщик')),
                ('plot', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='charge_obligations', to='water.landplot', verbose_name='Участок-плательщик')),
            ],
            options={
                'verbose_name': 'Основание финансового обязательства',
                'verbose_name_plural': 'Основания финансовых обязательств',
                'ordering': ['charge_id'],
                'constraints': [
                    models.CheckConstraint(condition=models.Q(('relief_amount__lte', models.F('base_amount'))), name='charge_obligation_relief_lte_base'),
                    models.CheckConstraint(
                        condition=(
                            models.Q(('membership__isnull', True), ('payer_scope', 'account'), ('person__isnull', True), ('plot__isnull', True))
                            | models.Q(('membership__isnull', True), ('payer_scope', 'plot'), ('person__isnull', True), ('plot__isnull', False))
                            | models.Q(('membership__isnull', True), ('payer_scope', 'person'), ('person__isnull', False), ('plot__isnull', True))
                            | models.Q(('membership__isnull', False), ('payer_scope', 'membership'), ('person__isnull', True), ('plot__isnull', True))
                        ),
                        name='charge_obligation_payer_fields',
                    ),
                ],
            },
        ),
    ]
