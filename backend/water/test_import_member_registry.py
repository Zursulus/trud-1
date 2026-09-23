from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook, load_workbook

from water.models import Account, Person
from water.private_registry import MemberRegistryEntry


HEADERS = [
    '№ пользователя',
    'Телефон(ы) нормализованные',
    'Телефон исходный',
    'Email',
    'Кадастровый №',
    'Адрес участка',
    'Площадь, м²',
    'Год вступления (точный)',
    'Источник года/основание',
    'Статус',
    'Legacy Account ID',
]


def make_registry(path, extra_rows=None):
    book = Workbook()
    sheet = book.active
    sheet.title = 'Закрытый реестр'
    sheet.append(HEADERS)
    for number in range(1, 301):
        sheet.append([
            number,
            f'+7 900 000-{number // 100:02d}-{number % 100:02d}',
            '',
            f'user{number}@example.test',
            '',
            f'Улица {number}',
            600,
            2020,
            '2020',
            'ГОТОВО',
            '',
        ])
    for row in extra_rows or []:
        sheet.append(row)
    book.save(path)


def set_legacy_account(path, resident_number, account_id):
    book = load_workbook(path)
    sheet = book['Закрытый реестр']
    header = [cell.value for cell in sheet[1]]
    account_col = header.index('Legacy Account ID') + 1
    for row_number in range(2, sheet.max_row + 1):
        if sheet.cell(row=row_number, column=1).value == resident_number:
            sheet.cell(row=row_number, column=account_col).value = account_id
            book.save(path)
            return
    raise AssertionError(f'№{resident_number} not found')


class ImportMemberRegistryTests(TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.path = Path(self.tempdir.name) / 'registry.xlsx'
        make_registry(self.path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_dry_run_changes_nothing_and_outputs_no_private_values(self):
        output = StringIO()
        call_command('import_member_registry', str(self.path), stdout=output)
        self.assertEqual(MemberRegistryEntry.objects.count(), 0)
        self.assertIn('DRY-RUN', output.getvalue())
        self.assertNotIn('user1@example.test', output.getvalue())
        self.assertNotIn('+7 900', output.getvalue())

    def test_apply_creates_number_keyed_registry_without_person(self):
        unique = Account.objects.create(number='A-1', plot='Улица 1')
        Account.objects.create(number='A-2a', plot='Улица 2')
        Account.objects.create(number='A-2b', plot='Улица 2')

        output = StringIO()
        call_command(
            'import_member_registry', str(self.path), apply=True, stdout=output,
        )

        self.assertEqual(MemberRegistryEntry.objects.count(), 300)
        self.assertEqual(Person.objects.count(), 0)
        first = MemberRegistryEntry.objects.get(pk=1)
        second = MemberRegistryEntry.objects.get(pk=2)
        self.assertEqual(first.account_id, unique.pk)
        self.assertIsNone(second.account_id)
        self.assertIsNone(first.person_id)
        self.assertEqual(first.email, 'user1@example.test')
        self.assertFalse(MemberRegistryEntry.objects.filter(pk=333).exists())
        self.assertIn('№333 не затронут', output.getvalue())

        second_run = StringIO()
        call_command(
            'import_member_registry', str(self.path), apply=True, stdout=second_run,
        )
        self.assertEqual(MemberRegistryEntry.objects.count(), 300)

    def test_optional_reserved_305_is_accepted_and_linked_without_person(self):
        reserve_account = Account.objects.create(number='LEGACY-305', plot='Другой legacy адрес')
        make_registry(self.path, extra_rows=[[
            305,
            '',
            '',
            '',
            '',
            'Миндальная 20',
            '',
            '',
            'Временный legacy-резерв; Account 348; без переноса ФИО/телефона',
            'ВРЕМЕННЫЙ РЕЗЕРВ',
            reserve_account.pk,
        ]])

        output = StringIO()
        call_command(
            'import_member_registry', str(self.path), apply=True, stdout=output,
        )

        self.assertEqual(MemberRegistryEntry.objects.count(), 301)
        reserve = MemberRegistryEntry.objects.get(pk=305)
        self.assertEqual(reserve.account_id, reserve_account.pk)
        self.assertIsNone(reserve.person_id)
        self.assertEqual(reserve.phone, '')
        self.assertEqual(reserve.email, '')
        self.assertIn('Дополнительные резервные №: 305', output.getvalue())
        self.assertIn('Из них явных Legacy Account ID: 1', output.getvalue())
        self.assertFalse(MemberRegistryEntry.objects.filter(pk=333).exists())

    def test_explicit_legacy_account_can_be_shared_by_split_registry_numbers(self):
        shared = Account.objects.create(number='LEGACY-19', plot='Лесная 19')
        set_legacy_account(self.path, 27, shared.pk)
        set_legacy_account(self.path, 28, shared.pk)

        output = StringIO()
        call_command(
            'import_member_registry', str(self.path), apply=True, stdout=output,
        )

        self.assertEqual(MemberRegistryEntry.objects.get(pk=27).account_id, shared.pk)
        self.assertEqual(MemberRegistryEntry.objects.get(pk=28).account_id, shared.pk)
        self.assertIn('Из них явных Legacy Account ID: 2', output.getvalue())

    def test_unknown_explicit_legacy_account_stops_import(self):
        set_legacy_account(self.path, 1, 999999)
        with self.assertRaisesMessage(Exception, 'Legacy Account ID 999999'):
            call_command(
                'import_member_registry', str(self.path), apply=True,
            )
        self.assertEqual(MemberRegistryEntry.objects.count(), 0)
