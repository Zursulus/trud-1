from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from openpyxl import Workbook

from .package_imports import SHEETS, inspect_water_package


def make_workbook_with_duplicate_addresses():
    book = Workbook()
    book.remove(book.active)
    rows = {
        'Участки': [
            ['TEST-PLOT-001', 'Лесная 43', 'Лесная 43', '', '', '', 'src', '2', 'готово'],
            ['TEST-PLOT-002', 'Лесная 43', 'Лесная 43', '', '', '', 'src', '3', 'готово'],
        ],
        'Люди': [],
        'Узлы': [['Тестовый узел', '']],
        'Группы': [['Тестовая линия', 'Тестовый узел', 'unknown', '']],
        'Состав групп': [],
        'Индивидуальные счетчики': [],
        'Общие и контрольные': [],
        'Показания': [],
    }
    for name, header in SHEETS.items():
        sheet = book.create_sheet(name)
        sheet.append(list(header))
        for row in rows[name]:
            sheet.append(row)
    output = BytesIO()
    book.save(output)
    book.close()
    return SimpleUploadedFile('water.xlsx', output.getvalue())


class WaterPackageAddressUniquenessTests(SimpleTestCase):
    def test_duplicate_plot_address_is_blocking(self):
        report = inspect_water_package(make_workbook_with_duplicate_addresses())

        self.assertFalse(report['ready'])
        self.assertTrue(
            any(
                'address «Лесная 43» повторяется 2 раза' in issue
                for issue in report['blocking_issues']
            )
        )
