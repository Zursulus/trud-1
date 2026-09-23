import secrets

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from water.models import Account, ResidentAccess, User
from water.resident_numbers import ResidentNumberSlot, TEST_RESIDENT_NUMBER


class Command(BaseCommand):
    help = 'Создаёт/проверяет тестовую учётку жителя №333 и выдаёт ей явный доступ к одному лицевому счёту.'

    def add_arguments(self, parser):
        parser.add_argument('--account-id', type=int, help='ID лицевого счёта, к которому разрешён тестовый доступ')
        parser.add_argument('--username', default='test333', help='Логин тестового пользователя (по умолчанию test333)')
        parser.add_argument('--reset-password', action='store_true', help='Сгенерировать новый временный пароль для уже созданной учётки')
        parser.add_argument('--list-accounts', action='store_true', help='Показать только ID/номер/участок доступных лицевых счетов и ничего не менять')

    def handle(self, *args, **options):
        if options['list_accounts']:
            for account in Account.objects.filter(archived=False).order_by('id'):
                self.stdout.write(f'{account.pk}\t{account.number or "—"}\t{account.plot or "—"}')
            return

        account_id = options.get('account_id')
        if not account_id:
            raise CommandError('Укажите --account-id. Тестовую учётку нельзя привязывать к чужому счёту наугад.')

        username = options['username'].strip()
        if not username:
            raise CommandError('Логин не может быть пустым.')

        temporary_password = None
        with transaction.atomic():
            try:
                account = Account.objects.select_for_update().get(pk=account_id, archived=False)
            except Account.DoesNotExist as error:
                raise CommandError('Действующий лицевой счёт с таким ID не найден.') from error

            try:
                # Lock only the slot row. Joining the nullable user relation here
                # would produce a LEFT JOIN, which PostgreSQL cannot lock with
                # SELECT ... FOR UPDATE on the nullable side.
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
                if user.first_name or user.last_name:
                    raise CommandError(
                        'У существующей тестовой учётки заполнены имя/фамилия. '
                        'Автоматически стирать их нельзя: сначала проверьте, что это действительно синтетическая запись.'
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
            f'Готово: пользователь №333, логин {user.username}, доступ к счёту ID {account.pk} ({account.number or "без номера"}).'
        ))
        if temporary_password:
            self.stdout.write(f'Временный пароль: {temporary_password}')
            self.stdout.write('Сохраните пароль сейчас: команда не записывает его в файлы или GitHub.')
        else:
            self.stdout.write('Пароль не менялся. Для нового временного пароля повторите с --reset-password.')


def models_q_active(today):
    from django.db.models import Q
    return Q(ends__isnull=True) | Q(ends__gt=today)
