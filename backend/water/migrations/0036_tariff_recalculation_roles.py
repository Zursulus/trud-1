from django.db import migrations


PERMISSIONS = (
    ('tariffrecalculation', 'view_tariffrecalculation', 'Can view Пересчёт тарифа водоканала'),
    ('tariffrecalculation', 'add_tariffrecalculation', 'Can add Пересчёт тарифа водоканала'),
    ('tariffrecalculation', 'approve_tariffrecalculation', 'Может утвердить пересчёт тарифа водоканала'),
    ('historicaltariffrecalculation', 'view_historicaltariffrecalculation', 'Can view historical Пересчёт тарифа водоканала'),
)


def grant(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    alias = schema_editor.connection.alias
    group = Group.objects.using(alias).filter(name='Администратор ТСН').first()
    if group is None:
        return  # setup_roles creates/synchronizes roles on fresh installations.
    for model, code, name in PERMISSIONS:
        content_type, _ = ContentType.objects.using(alias).get_or_create(app_label='water', model=model)
        permission, _ = Permission.objects.using(alias).get_or_create(
            content_type=content_type, codename=code, defaults={'name': name},
        )
        group.permissions.add(permission)


def revoke(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    alias = schema_editor.connection.alias
    group = Group.objects.using(alias).filter(name='Администратор ТСН').first()
    if group:
        group.permissions.remove(*Permission.objects.using(alias).filter(
            content_type__app_label='water', codename__in=[code for _, code, _ in PERMISSIONS],
        ))


class Migration(migrations.Migration):
    dependencies = [('water', '0035_tariff_recalculation')]
    operations = [migrations.RunPython(grant, revoke)]
