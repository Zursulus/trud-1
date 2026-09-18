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

    def test_unknown_group_and_missing_date_require_review(self):
        def mutate(book):
            book['Состав групп']['B2'] = 'Несуществующая группа'
            book['Показания']['B2'] = ''
        report = inspect_water_package(self.workbook(mutate))
        self.assertFalse(report['ready'])
        self.assertTrue(any('неизвестная группа' in issue for issue in report['issues']))
        self.assertTrue(any('дата неизвестна' in issue for issue in report['issues']))

    def test_missing_required_sheet_is_rejected(self):
        def mutate(book):
            del book['Показания']
        with self.assertRaises(ValidationError):
            inspect_water_package(self.workbook(mutate))
