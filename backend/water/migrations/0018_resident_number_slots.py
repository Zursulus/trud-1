from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def reserve_resident_numbers(apps, schema_editor):
    Slot = apps.get_model('water', 'ResidentNumberSlot')
    slots = [
        Slot(number=number, purpose='resident', notes='Зарезервировано для реального пользователя ТСН')
        for number in range(1, 311)
    ]
    slots.append(
        Slot(number=333, purpose='test', notes='Зарезервировано для тестовой учётной записи владельца')
    )
    Slot.objects.bulk_create(slots, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ('water', '0017_resident_appeal_attachments_and_view_state'),
    ]

    operations = [
        migrations.CreateModel(
            name='ResidentNumberSlot',
            fields=[
                ('number', models.PositiveSmallIntegerField(primary_key=True, serialize=False, verbose_name='Номер пользователя')),
                ('purpose', models.CharField(choices=[('resident', 'Житель'), ('test', 'Тестовая учётная запись')], max_length=20, verbose_name='Назначение')),
                ('assigned_at', models.DateTimeField(blank=True, editable=False, null=True, verbose_name='Назначен')),
                ('notes', models.CharField(blank=True, max_length=300, verbose_name='Примечание')),
                ('user', models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='resident_number_slot', to=settings.AUTH_USER_MODEL, verbose_name='Учётная запись')),
            ],
            options={
                'verbose_name': 'Номер пользователя',
                'verbose_name_plural': 'Номера пользователей',
                'ordering': ['number'],
            },
        ),
        migrations.RunPython(reserve_resident_numbers, migrations.RunPython.noop),
    ]
