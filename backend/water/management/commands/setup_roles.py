from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

WATER = ('account', 'supplynode', 'watergroup', 'membership', 'meter', 'reading', 'groupconsumption')
REGISTRY = ('person', 'landplot', 'plotrelation')
ADMIN = 'Администратор СНТ'
OPERATOR = 'Оператор воды'


class Command(BaseCommand):
    help = 'Создать/синхронизировать две штатные роли; пользователей не менять.'

    @transaction.atomic
    def handle(self, *args, **options):
        water_view = {f'view_{name}' for name in WATER}
        water_view |= {f'view_historical{name}' for name in WATER}
        registry = {f'{action}_{name}' for name in REGISTRY for action in ('view', 'add', 'change')}
        registry |= {f'view_historical{name}' for name in REGISTRY}
        operator = water_view | {'add_reading', 'add_groupconsumption'}
        administrator = water_view | registry | {f'{action}_{name}' for name in WATER for action in ('add', 'change')}
        administrator.add('export_account')
        for name, codes in ((ADMIN, administrator), (OPERATOR, operator)):
            permissions = list(Permission.objects.filter(content_type__app_label='water', codename__in=codes))
            if len(permissions) != len(codes):
                raise RuntimeError('Сначала выполните migrate: не найдены все права.')
            if name == ADMIN:
                permissions.append(Permission.objects.get(content_type__app_label='admin', codename='view_logentry'))
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set(permissions)
            self.stdout.write(f'{name}: {len(permissions)} прав')
