from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from openpyxl import Workbook

from .models import Account, LandPlot, Meter, Person, SupplyNode, WaterGroup
from .package_imports import SHEETS, inspect_water_package
from .package_writer import import_verified_water_package


def make_workbook(mutate=None):
    book = Workbook()
    book.remove(book.active)
    rows = {
        'Участки': [['TEST-PLOT-001', 'Лесная 1', 'Лесная 1', '', 'Тестовый пользователь', 'TEST-PHONE', 'src', '2', 'готово']],
        'Люди': [['TEST-PERSON-001', 'Тестовый пользователь', 'TEST-PHONE', 'TEST-PLOT-001', 'owner', '']],
        'Узлы': [['Тестовый узел', '']],
        'Группы': [['Тестовая линия', 'Тестовый узел', 'unknown', '']],
        'Состав групп': [['TEST-PLOT-001', 'Тестовая линия', '2026-01-01', '', '']],
        'Индивидуальные счетчики': [['TEST-METER-001', 'individual', 'Тестовый узел', 'Тестовая линия', 'TEST-PLOT-001', 'TEST-SERIAL-001', '']],
        'Общие и контрольные': [['TEST-METER-002', 'line', 'Тестовый узел', 'Тестовая линия', 'TEST-SERIAL-002', 'src', 'src', '2', 'готово']],
        'Показания': [['TEST-METER-001', '2026-09-01', '10.5', '', 'src', '2']],
    }
    for name, header in SHEETS.items():
        sheet = book.create_sheet(name)
        sheet.append(list(header))
        for row in rows[name]:
            sheet.append(row)
    if mutate:
        mutate(book)
    output = BytesIO()
    book.save(output)
    return SimpleUploadedFile('water.xlsx', output.getvalue())


class WaterPackageImportTests(SimpleTestCase):
    def workbook(self, mutate=None):
        return make_workbook(mutate)

    def test_valid_package_is_read_only_and_ready(self):
        report = inspect_water_package(self.workbook())
        self.assertTrue(report['ready'])
        self.assertEqual(report['counts']['Участки'], 1)
        self.assertEqual(report['counts']['Показания'], 1)
        self.assertEqual(report['reading_plan']['dated'], 1)
        self.assertEqual(report['blocking_issues'], [])

    def test_unknown_group_is_blocking_but_undated_value_is_preserved_for_review(self):
        def mutate(book):
            book['Состав групп']['B2'] = 'Несуществующая группа'
            book['Показания']['B2'] = ''
        report = inspect_water_package(self.workbook(mutate))
        self.assertFalse(report['ready'])
        self.assertTrue(any('неизвестная группа' in issue for issue in report['blocking_issues']))
        self.assertEqual(report['reading_plan']['undated_to_meter_notes'], 1)
        self.assertTrue(any('Reading не создавать' in note for note in report['review_notes']))

    def test_undated_valid_value_does_not_block_structure_import(self):
        def mutate(book):
            book['Показания']['B2'] = ''
        report = inspect_water_package(self.workbook(mutate))
        self.assertTrue(report['ready'])
        self.assertTrue(report['structure_ready'])
        self.assertEqual(report['blocking_issues'], [])
        self.assertEqual(report['reading_plan']['dated'], 0)
        self.assertEqual(report['reading_plan']['undated_to_meter_notes'], 1)
        self.assertEqual(report['reading_plan']['empty_skipped'], 0)

    def test_empty_value_is_skipped_without_creating_fake_zero(self):
        def mutate(book):
            book['Показания']['B2'] = ''
            book['Показания']['C2'] = ''
        report = inspect_water_package(self.workbook(mutate))
        self.assertTrue(report['ready'])
        self.assertEqual(report['reading_plan']['dated'], 0)
        self.assertEqual(report['reading_plan']['undated_to_meter_notes'], 0)
        self.assertEqual(report['reading_plan']['empty_skipped'], 1)
        self.assertTrue(any('показание не создаётся' in note for note in report['review_notes']))

    def test_missing_required_sheet_is_rejected(self):
        def mutate(book):
            del book['Показания']
        with self.assertRaises(ValidationError):
            inspect_water_package(self.workbook(mutate))

    def test_repeated_service_header_is_not_treated_as_data(self):
        def mutate(book):
            sheet = book['Общие и контрольные']
            sheet.insert_rows(2)
            for column, value in enumerate(SHEETS['Общие и контрольные'], 1):
                sheet.cell(row=2, column=column, value=value)

        report = inspect_water_package(self.workbook(mutate))

        self.assertTrue(report['ready'])
        self.assertEqual(report['counts']['Общие и контрольные'], 1)
        self.assertEqual(report['blocking_issues'], [])

    def test_phone_number_in_reading_value_is_blocking(self):
        def mutate(book):
            book['Показания']['B2'] = ''
            book['Показания']['C2'] = '79991234567'

        report = inspect_water_package(self.workbook(mutate))

        self.assertFalse(report['ready'])
        self.assertTrue(any('похоже на телефон' in issue for issue in report['blocking_issues']))
        self.assertEqual(report['reading_plan']['undated_to_meter_notes'], 0)


class WaterPackageWriterTests(TestCase):
    def test_writer_creates_verified_structure_and_defers_dated_reading(self):
        result = import_verified_water_package(make_workbook())
        self.assertEqual(result['accounts'], 1)
        self.assertEqual(result['plots'], 1)
        self.assertEqual(result['people'], 1)
        self.assertEqual(result['nodes'], 1)
        self.assertEqual(result['groups'], 1)
        self.assertEqual(result['meters'], 2)
        self.assertEqual(result['dated_values_deferred'], 1)
        self.assertEqual(Account.objects.count(), 1)
        self.assertEqual(LandPlot.objects.count(), 1)
        self.assertEqual(Person.objects.count(), 1)
        self.assertEqual(SupplyNode.objects.count(), 1)
        self.assertEqual(WaterGroup.objects.count(), 1)
        self.assertEqual(Meter.objects.count(), 2)
        self.assertIn('Датированное исходное показание', Meter.objects.get(serial='TEST-SERIAL-001').notes)

    def test_undated_value_is_preserved_verbatim_in_meter_notes(self):
        def mutate(book):
            book['Показания']['B2'] = ''
            book['Показания']['C2'] = '123.45'
        result = import_verified_water_package(make_workbook(mutate))
        self.assertEqual(result['undated_values_preserved'], 1)
        self.assertIn('123.45', Meter.objects.get(serial='TEST-SERIAL-001').notes)

    def test_nonempty_database_is_rejected_without_changes(self):
        Account.objects.create(number='existing', plot='existing')
        with self.assertRaises(ValidationError):
            import_verified_water_package(make_workbook())
        self.assertEqual(Account.objects.count(), 1)
        self.assertEqual(LandPlot.objects.count(), 0)
        self.assertEqual(Meter.objects.count(), 0)

    def test_writer_skips_repeated_service_header_by_column_signature(self):
        def mutate(book):
            sheet = book['Общие и контрольные']
            sheet.insert_rows(2)
            for column, value in enumerate(SHEETS['Общие и контрольные'], 1):
                sheet.cell(row=2, column=column, value=value)

        result = import_verified_water_package(make_workbook(mutate))

        self.assertEqual(result['meters'], 2)
        self.assertEqual(Meter.objects.count(), 2)
        self.assertFalse(Meter.objects.filter(serial='meter_id').exists())

    def test_blocking_package_rolls_back_without_partial_rows(self):
        def mutate(book):
            book['Группы']['B2'] = 'Несуществующий узел'
        with self.assertRaises(ValidationError):
            import_verified_water_package(make_workbook(mutate))
        self.assertEqual(Account.objects.count(), 0)
        self.assertEqual(LandPlot.objects.count(), 0)
        self.assertEqual(Person.objects.count(), 0)
        self.assertEqual(Meter.objects.count(), 0)
