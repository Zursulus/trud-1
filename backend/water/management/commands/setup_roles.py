from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

BUSINESS = ('account', 'supplynode', 'watergroup', 'membership', 'meter', 'reading', 'groupconsumption')
ADMIN = 'Администратор СНТ'
OPERATOR = 'Оператор воды'


class Command(BaseCommand):
    help = 'Создать/синхронизировать две штатные роли; пользователей не менять.'

    @transaction.atomic
    def handle(self, *args, **options):
        common = {f'view_{name}' for name in BUSINESS}
        common |= {f'view_historical{name}' for name in BUSINESS}
        operator = common | {'add_reading', 'add_groupconsumption'}
        administrator = common | {f'{action}_{name}' for name in BUSINESS for action in ('add', 'change')}
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
