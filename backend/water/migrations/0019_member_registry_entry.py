from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('water', '0018_resident_number_slots'),
    ]

    operations = [
        migrations.CreateModel(
            name='MemberRegistryEntry',
            fields=[
                (
                    'resident_number',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        primary_key=True,
                        related_name='member_registry_entry',
                        serialize=False,
                        to='water.residentnumberslot',
                        verbose_name='№ пользователя',
                    ),
                ),
                ('created_at', models.DateTimeField(auto_now_add=True, editable=False, verbose_name='Связь создана')),
                (
                    'person',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name='member_registry_entry',
                        to='water.person',
                        verbose_name='Человек',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Связь № пользователя с человеком',
                'verbose_name_plural': 'Закрытый реестр · номера и люди',
                'ordering': ['resident_number_id'],
                'permissions': [
                    ('access_private_registry', 'Может работать с закрытым реестром членов ТСН'),
                ],
            },
        ),
    ]
