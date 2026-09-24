from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.db import transaction

WATER = ('account', 'supplynode', 'watergroup', 'membership', 'meter', 'reading', 'groupconsumption')
REGISTRY = (
    'landplot', 'residentaccess', 'residentinvite', 'residentpasswordreset',
    'appealcategory', 'residentappeal', 'residentappealmessage', 'documentcategory', 'accountdocument',
)
PRIVATE_RECORDED = ('person', 'plotrelation', 'importbatch', 'importrow')
PRIVATE_MODELS = (*PRIVATE_RECORDED, 'memberregistryentry')
FINANCE = ('billingpolicy', 'billingassignment', 'tariff', 'billingperiod', 'charge', 'payment', 'paymentallocation')
PUBLIC_SITE = ('publicnews', 'publicdocumentcategory', 'publicdocument')
ADMIN = 'Администратор ТСН'
LEGACY_ADMIN = 'Администратор СНТ'
OPERATOR = 'Оператор воды'
CONTROLLER = 'Контролёр воды'
PRIVATE_REGISTRY = 'Закрытый реестр членов ТСН'


class Command(BaseCommand):
    help = 'Создать/синхронизировать штатные роли; пользователей не менять.'

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
        controller = {'view_controllerreadingsubmission', 'add_controllerreadingsubmission'}
        finance = {f'{action}_{name}' for name in FINANCE for action in ('view', 'add', 'change')}
        finance |= {f'view_historical{name}' for name in FINANCE}
        public_site = {f'{action}_{name}' for name in PUBLIC_SITE for action in ('view', 'add', 'change')}

        private_registry = {
            f'{action}_{name}' for name in PRIVATE_MODELS for action in ('view', 'add', 'change')
        }
        private_registry |= {f'view_historical{name}' for name in PRIVATE_RECORDED}
        private_registry |= {
            'access_private_registry',
            'view_account',
            'view_landplot',
        }

        administrator = water_view | registry | finance | {
            f'{action}_{name}' for name in WATER for action in ('add', 'change')
        }
        administrator.update({f'{action}_controllerreadingsubmission' for action in ('view', 'add', 'change')})
        administrator.update({'export_account', 'export_reading', 'view_chargeobligation'})

        role_codes = (
            (ADMIN, administrator),
            (OPERATOR, operator),
            (CONTROLLER, controller),
            (PRIVATE_REGISTRY, private_registry),
        )
        for name, codes in role_codes:
            permissions = list(Permission.objects.filter(content_type__app_label='water', codename__in=codes))
            expected = len(codes)
            if name == ADMIN:
                public_permissions = list(Permission.objects.filter(
                    content_type__app_label='public_site', codename__in=public_site,
                ))
                permissions.extend(public_permissions)
                expected += len(public_site)
            if len(permissions) != expected:
                missing = sorted(codes - {permission.codename for permission in permissions})
                raise RuntimeError(f'Сначала выполните migrate: не найдены все права: {missing}')
            if name == ADMIN:
                permissions.append(Permission.objects.get(content_type__app_label='admin', codename='view_logentry'))
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set(permissions)
            self.stdout.write(f'{name}: {len(permissions)} прав')
