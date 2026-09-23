from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from water.models import Account, ImportRow, Person, User
from water.private_registry import MemberRegistryEntry
from water.resident_numbers import ResidentNumberSlot, TEST_RESIDENT_NUMBER


class Command(BaseCommand):
    help = 'Показывает только агрегаты по границе персональных данных; значения ФИО/телефонов/email не выводит.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--strict', action='store_true',
            help='Завершиться ошибкой, если в рабочих Account или resident User остаются legacy-поля ПД.',
        )

    def handle(self, *args, **options):
        account_contact_rows = Account.objects.filter(
            Q(contact_name__gt='') | Q(phone__gt='')
        ).count()
        import_pii_rows = ImportRow.objects.filter(
            Q(person_name__gt='') | Q(phone__gt='') | Q(email__gt='')
        ).count()
        resident_named_users = User.objects.filter(is_staff=False).filter(
            Q(first_name__gt='') | Q(last_name__gt='')
        ).count()
        resident_email_users = User.objects.filter(is_staff=False).exclude(email='').count()
        people = Person.objects.filter(archived=False).count()
        mapped_people = MemberRegistryEntry.objects.count()
        resident_slots = ResidentNumberSlot.objects.filter(purpose=ResidentNumberSlot.PURPOSE_RESIDENT).count()
        assigned_resident_slots = ResidentNumberSlot.objects.filter(
            purpose=ResidentNumberSlot.PURPOSE_RESIDENT,
            user__isnull=False,
        ).count()
        test_slot = ResidentNumberSlot.objects.filter(pk=TEST_RESIDENT_NUMBER).first()

        rows = (
            ('Действующих Person', people),
            ('Связей закрытого реестра №↔Person', mapped_people),
            ('Зарезервированных resident-номеров', resident_slots),
            ('Resident-номеров с учёткой входа', assigned_resident_slots),
            ('Account с legacy ФИО/телефоном', account_contact_rows),
            ('ImportRow с ФИО/телефоном/email', import_pii_rows),
            ('Resident User с first_name/last_name', resident_named_users),
            ('Resident User с техническим email', resident_email_users),
            ('Тестовый №333 связан с User', int(bool(test_slot and test_slot.user_id))),
        )
        for label, value in rows:
            self.stdout.write(f'{label}: {value}')

        self.stdout.write('Значения персональных данных намеренно не выводятся.')

        if options['strict'] and (account_contact_rows or resident_named_users):
            raise CommandError(
                'Граница ещё не чистая: найдены legacy ПД в Account или имя/фамилия в resident User. '
                'Автоматическое удаление запрещено; требуется проверяемая миграция.'
            )
