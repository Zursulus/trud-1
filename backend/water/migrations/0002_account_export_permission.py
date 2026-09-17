from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('water', '0001_initial')]
    operations = [migrations.AlterModelOptions(
        name='account',
        options={'ordering': ['id'], 'verbose_name': 'Лицевой счёт',
                 'verbose_name_plural': '01 · Лицевые счета',
                 'permissions': [('export_account', 'Может выгружать карточки в CSV')]},
    )]
