from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('water', '0019_member_registry_entry'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='memberregistryentry',
            options={
                'ordering': ['resident_number_id'],
                'permissions': [('access_private_registry', 'Может работать с закрытым реестром членов ТСН')],
                'verbose_name': 'Запись закрытого реестра',
                'verbose_name_plural': 'Закрытый реестр · номера и контакты',
            },
        ),
        migrations.AlterField(
            model_name='memberregistryentry',
            name='person',
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='member_registry_entry',
                to='water.person',
                verbose_name='Человек / ФИО (если юридически необходимо)',
            ),
        ),
        migrations.AddField(
            model_name='memberregistryentry',
            name='account',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='member_registry_entries',
                to='water.account',
                verbose_name='Рабочий лицевой счёт',
            ),
        ),
        migrations.AddField(
            model_name='memberregistryentry',
            name='phone',
            field=models.CharField(blank=True, max_length=160, verbose_name='Телефон'),
        ),
        migrations.AddField(
            model_name='memberregistryentry',
            name='email',
            field=models.EmailField(blank=True, max_length=254, verbose_name='Электронная почта'),
        ),
        migrations.AddField(
            model_name='memberregistryentry',
            name='joined_year',
            field=models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='Год вступления'),
        ),
        migrations.AddField(
            model_name='memberregistryentry',
            name='membership_note',
            field=models.CharField(blank=True, max_length=500, verbose_name='Основание / исходная пометка'),
        ),
    ]
