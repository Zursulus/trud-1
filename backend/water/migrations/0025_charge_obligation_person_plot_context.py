# Generated for ZUR-54 on 2026-09-24

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('water', '0024_charge_obligation'),
    ]

    operations = [
        migrations.AlterField(
            model_name='chargeobligation',
            name='plot',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='charge_obligations',
                to='water.landplot',
                verbose_name='Участок обязательства',
            ),
        ),
        migrations.AlterField(
            model_name='historicalchargeobligation',
            name='plot',
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name='+',
                to='water.landplot',
                verbose_name='Участок обязательства',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='chargeobligation',
            name='charge_obligation_payer_fields',
        ),
        migrations.AddConstraint(
            model_name='chargeobligation',
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(
                        payer_scope='account',
                        plot__isnull=True,
                        person__isnull=True,
                        membership__isnull=True,
                    )
                    | models.Q(
                        payer_scope='plot',
                        plot__isnull=False,
                        person__isnull=True,
                        membership__isnull=True,
                    )
                    | models.Q(
                        payer_scope='person',
                        person__isnull=False,
                        membership__isnull=True,
                    )
                    | models.Q(
                        payer_scope='membership',
                        person__isnull=True,
                        membership__isnull=False,
                    )
                ),
                name='charge_obligation_payer_fields',
            ),
        ),
    ]
