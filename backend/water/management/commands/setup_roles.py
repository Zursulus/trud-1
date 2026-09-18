from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

WATER = ('account', 'supplynode', 'watergroup', 'membership', 'meter', 'reading', 'groupconsumption')
REGISTRY = ('person', 'landplot', 'plotrelation', 'importbatch', 'importrow', 'residentaccess', 'residentinvite')
FINANCE = ('billingpolicy', 'billingassignment', 'tariff', 'billingperiod', 'charge', 'payment', 'paymentallocation')
ADMIN = 'Администратор ТСН'
LEGACY_ADMIN = 'Администратор СНТ'
OPERATOR = 'Оператор воды'


class Command(BaseCommand):
    help = 'Создать/синхронизировать две штатные роли; пользователей не менять.'

    @transaction.atomic
    def handle(self, *args, **options):
        legacy = Group.objects.filter(name=LEGACY_ADMIN).first()
        current = Group.objects.filter(name=ADMIN).first()
        if legacy is not None and current is None:
            legacy.name = ADMIN
            legacy.save(update_fields=['name'])
        elif legacy is not None:
            current.user_set.add(*legacy.user_set.all())
            legacy.delete()

        water_view = {f'view_{name}' for name in WATER}
        water_view |= {f'view_historical{name}' for name in WATER}
        registry = {f'{action}_{name}' for name in REGISTRY for action in ('view', 'add', 'change')}
        registry |= {f'view_historical{name}' for name in REGISTRY}
        operator = water_view | {'add_reading', 'add_groupconsumption'}
        finance = {f'{action}_{name}' for name in FINANCE for action in ('view', 'add', 'change')}
        finance |= {f'view_historical{name}' for name in FINANCE}
        administrator = water_view | registry | finance | {f'{action}_{name}' for name in WATER for action in ('add', 'change')}
        administrator.update({'export_account', 'export_reading'})
        for name, codes in ((ADMIN, administrator), (OPERATOR, operator)):
            permissions = list(Permission.objects.filter(content_type__app_label='water', codename__in=codes))
            if len(permissions) != len(codes):
                raise RuntimeError('Сначала выполните migrate: не найдены все права.')
            if name == ADMIN:
                permissions.append(Permission.objects.get(content_type__app_label='admin', codename='view_logentry'))
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set(permissions)
            self.stdout.write(f'{name}: {len(permissions)} прав')
