from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('water', '0032_access_control_v2_assignments'),
    ]

    operations = [
        migrations.AddField(
            model_name='residentaccessrequest',
            name='requester_user',
            field=models.ForeignKey(
                blank=True,
                editable=False,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='submitted_resident_access_requests',
                to=settings.AUTH_USER_MODEL,
                verbose_name='Кабинет-заявитель',
            ),
        ),
    ]
