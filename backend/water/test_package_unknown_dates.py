from django.test import SimpleTestCase

from .package_imports import inspect_water_package
from .test_package_imports import make_workbook


class WaterPackageUnknownDateGuardTests(SimpleTestCase):
    def test_explicit_date_is_blocking_when_note_says_exact_date_unknown(self):
        def mutate(book):
            book['Показания']['B2'] = '2026-06-05'
            book['Показания']['D2'] = 'Исходное состояние счётчика: июнь; точная дата неизвестна'

        report = inspect_water_package(make_workbook(mutate))

        self.assertFalse(report['ready'])
        self.assertTrue(
            any(
                'нельзя подставлять техническую дату' in issue
                for issue in report['blocking_issues']
            )
        )

    def test_real_dated_reading_without_unknown_date_note_remains_valid(self):
        report = inspect_water_package(make_workbook())

        self.assertTrue(report['ready'])
        self.assertEqual(report['reading_plan']['dated'], 1)
