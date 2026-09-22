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
                'не соответствует согласованной технической схеме 5/20' in issue
                for issue in report['blocking_issues']
            )
        )

    def test_agreed_technical_previous_date_is_allowed(self):
        def mutate(book):
            book['Показания']['B2'] = '2026-06-05'
            book['Показания']['D2'] = (
                'Исходное состояние счётчика: июнь, предыдущее; '
                'техническая дата по согласованной схеме 5/20: 05.06.2026 '
                '(предыдущее/начальное); точная дата в источнике неизвестна'
            )

        report = inspect_water_package(make_workbook(mutate))

        self.assertTrue(report['ready'])
        self.assertEqual(report['reading_plan']['technical_dated'], 1)
        self.assertEqual(report['reading_plan']['dated'], 1)

    def test_wrong_day_is_blocked_even_with_technical_marker(self):
        def mutate(book):
            book['Показания']['B2'] = '2026-06-20'
            book['Показания']['D2'] = (
                'Исходное состояние счётчика: июнь, предыдущее; '
                'техническая дата по согласованной схеме 5/20; '
                'точная дата в источнике неизвестна'
            )

        report = inspect_water_package(make_workbook(mutate))

        self.assertFalse(report['ready'])
        self.assertTrue(
            any(
                'не соответствует согласованной технической схеме 5/20' in issue
                for issue in report['blocking_issues']
            )
        )

    def test_real_dated_reading_without_unknown_date_note_remains_valid(self):
        report = inspect_water_package(make_workbook())

        self.assertTrue(report['ready'])
        self.assertEqual(report['reading_plan']['dated'], 1)
