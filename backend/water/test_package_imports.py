from io import BytesIO

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from openpyxl import Workbook

from .package_imports import SHEETS, inspect_water_package


class WaterPackageImportTests(SimpleTestCase):
    def workbook(self, mutate=None):
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
