from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.test import TestCase
from openpyxl import Workbook

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
]


def make_registry(path):
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
        ])
    book.save(path)


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
