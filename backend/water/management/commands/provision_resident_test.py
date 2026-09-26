import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from water.models import Account, ResidentAccess, User
from water.resident_numbers import ResidentNumberSlot, TEST_RESIDENT_NUMBER


TEST_ACCOUNT_NUMBER = 'TEST-333'
TEST_ACCOUNT_PLOT = 'Тестовый участок №333'
TEST_ACCOUNT_NOTE = 'Синтетический лицевой счёт для проверки личного кабинета пользователя №333'


class Command(BaseCommand):
    help = 'Создаёт/проверяет синтетическую тестовую учётку жителя №333 без ФИО и без доступа к реальному участку.'

    def add_arguments(self, parser):
        parser.add_argument('--account-id', type=int, help='ID уже существующего синтетического счёта TEST-333')
        parser.add_argument(
            '--ensure-synthetic-account', action='store_true',
            help='Создать или проверить отдельный синтетический лицевой счёт TEST-333',
        )
        parser.add_argument('--username', default='test333', help='Логин тестового пользователя (по умолчанию test333)')
        parser.add_argument('--reset-password', action='store_true', help='Сгенерировать новый временный пароль для уже созданной учётки')
        parser.add_argument('--list-accounts', action='store_true', help='Показать только пригодный синтетический счёт TEST-333 и ничего не менять')

    def handle(self, *args, **options):
        if options['list_accounts']:
            for account in Account.objects.filter(archived=False, number=TEST_ACCOUNT_NUMBER).order_by('id'):
                self.stdout.write(f'{account.pk}\t{account.number}\t{account.plot or "—"}')
            return

        username = options['username'].strip()
        if not username:
            raise CommandError('Логин не может быть пустым.')

        temporary_password = None
        with transaction.atomic():
            account = self._resolve_test_account(options)

            try:
                slot = ResidentNumberSlot.objects.select_for_update().get(
                    pk=TEST_RESIDENT_NUMBER,
                    purpose=ResidentNumberSlot.PURPOSE_TEST,
                )
            except ResidentNumberSlot.DoesNotExist as error:
                raise CommandError('Слот №333 не подготовлен. Сначала примените миграции ZUR-58.') from error

            if slot.user_id:
                user = User.objects.get(pk=slot.user_id)
                if user.is_staff:
                    raise CommandError('Слот №333 ошибочно связан с сотрудником; автоматическое исправление запрещено.')
                if user.first_name or user.last_name or user.email:
                    raise CommandError(
                        'У существующей тестовой учётки заполнены ФИО/email. '
                        'Автоматически стирать их нельзя: сначала проверьте синтетическую запись.'
                    )
                if options['reset_password']:
                    temporary_password = secrets.token_urlsafe(18)
                    user.set_password(temporary_password)
                    user.save(update_fields=['password'])
            else:
                if User.objects.filter(username=username).exists():
                    raise CommandError('Такой логин уже существует и не связан со слотом №333.')
                temporary_password = secrets.token_urlsafe(18)
                user = User.objects.create_user(
                    username=username,
                    password=temporary_password,
                    first_name='',
                    last_name='',
                    email='',
                    is_staff=False,
                    is_superuser=False,
                    is_active=True,
                )
                slot.user = user
                slot.assigned_at = timezone.now()
                slot.save(update_fields=['user', 'assigned_at'])

            today = timezone.localdate()
            active = ResidentAccess.objects.filter(
                user=user,
                account=account,
                starts__lte=today,
            ).filter(models_q_active(today)).first()
            if not active:
                access = ResidentAccess(
                    user=user,
                    account=account,
                    role='payer',
                    starts=today,
                    notes='Тестовая учётная запись №333 для проверки личного кабинета',
                )
                access._history_user = user
                access._change_reason = 'Тестовый доступ №333 создан служебной командой'
                access.save()

        self.stdout.write(self.style.SUCCESS(
            f'Готово: пользователь №333, логин {user.username}, доступ только к {TEST_ACCOUNT_NUMBER} (Account ID {account.pk}).'
        ))
        if temporary_password:
            self.stdout.write(f'Временный пароль: {temporary_password}')
            self.stdout.write('Сохраните пароль сейчас: команда не записывает его в файлы или GitHub.')
        else:
            self.stdout.write('Пароль не менялся. Для нового временного пароля повторите с --reset-password.')

    def _resolve_test_account(self, options):
        account_id = options.get('account_id')
        ensure = options.get('ensure_synthetic_account')
        if not account_id and not ensure:
            raise CommandError(
                'Укажите --ensure-synthetic-account. №333 нельзя привязывать к реальному лицевому счёту.'
            )

        if ensure:
            account, _created = Account.objects.select_for_update().get_or_create(
                number=TEST_ACCOUNT_NUMBER,
                defaults={
                    'plot': TEST_ACCOUNT_PLOT,
                    'contact_name': '',
                    'phone': '',
                    'notes': TEST_ACCOUNT_NOTE,
                    'archived': False,
                },
            )
        else:
            try:
                account = Account.objects.select_for_update().get(pk=account_id, archived=False)
            except Account.DoesNotExist as error:
                raise CommandError('Действующий лицевой счёт с таким ID не найден.') from error

        if account.number != TEST_ACCOUNT_NUMBER:
            raise CommandError('№333 разрешено привязывать только к синтетическому счёту TEST-333.')
        if account.archived:
            raise CommandError('Синтетический счёт TEST-333 архивирован.')
        if account.contact_name or account.phone:
            raise CommandError('В TEST-333 обнаружены ФИО/телефон. Использовать его как синтетический счёт нельзя.')
        if account.plot and account.plot != TEST_ACCOUNT_PLOT:
            raise CommandError('TEST-333 имеет неожиданное обозначение участка; автоматическое исправление запрещено.')
        if not account.plot:
            account.plot = TEST_ACCOUNT_PLOT
            account.notes = account.notes or TEST_ACCOUNT_NOTE
            account.save(update_fields=['plot', 'notes'])
        if account_id and account.pk != account_id:
            raise CommandError('--account-id не совпадает с синтетическим счётом TEST-333.')
        return account


def models_q_active(today):
    from django.db.models import Q
    return Q(ends__isnull=True) | Q(ends__gt=today)
