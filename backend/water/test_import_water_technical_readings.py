from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook

from water.models import Account, Meter, Reading, SupplyNode, WaterGroup


class TechnicalWaterReadingImportTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='TRUD-PLOT-0003', plot='Лесная 1')
        self.node = SupplyNode.objects.create(name='Узел без гидранта')
        self.group = WaterGroup.objects.create(name='Лесная без гидранта', node=self.node)
        self.meter = Meter.objects.create(
            serial='CURRENT-0003', kind='individual', node=self.node,
            account=self.account, group=self.group,
            notes='ID импорта: IND-TRUD-PLOT-0003',
        )
        Reading.objects.create(meter=self.meter, date='2026-08-01', value='428')

    def _package(self, *, old_scheme=False):
        book = Workbook()
        meter_sheet = book.active
        meter_sheet.title = 'Индивидуальные счетчики'
        meter_sheet.append(['meter_id', 'kind', 'node_name', 'group_name', 'plot_id', 'serial', 'notes'])
        meter_sheet.append([
            'IND-TRUD-PLOT-0003', 'individual', 'Узел без гидранта',
            'Лесная без гидранта', 'TRUD-PLOT-0003', '', '',
        ])
        meter_sheet.append([
            'IND-TRUD-PLOT-0003-H1', 'individual', 'Узел без гидранта',
            'Лесная без гидранта', 'TRUD-PLOT-0003', '',
            'Исторический прибор до сброса',
        ])

        readings = book.create_sheet('Показания')
        readings.append(['meter_id', 'date', 'value_m3', 'notes', 'source_sheet', 'source_row'])
        if old_scheme:
            readings.append([
                'IND-TRUD-PLOT-0003', '2026-08-05', 423,
                'Исходное состояние счётчика: август, предыдущее; '
                'техническая дата по согласованной схеме 5/20: 05.08.2026; '
                'точная дата в источнике неизвестна',
                'test', 1,
            ])
        else:
            readings.append([
                'IND-TRUD-PLOT-0003-H1', '2026-05-31', 100,
                'Исходное состояние счётчика: июнь, предыдущее; '
                'техническая дата по согласованной схеме граница/20: 31.05.2026 '
                '(предыдущее/начальное); точная дата в источнике неизвестна',
                'test', 1,
            ])
            readings.append([
                'IND-TRUD-PLOT-0003-H1', '2026-06-20', 110,
                'Исходное состояние счётчика: июнь, текущее; '
                'техническая дата по согласованной схеме граница/20: 20.06.2026 '
                '(текущее); точная дата в источнике неизвестна',
                'test', 1,
            ])
            readings.append([
                'IND-TRUD-PLOT-0003', '2026-07-31', 423,
                'Исходное состояние счётчика: август, предыдущее; '
                'техническая дата по согласованной схеме граница/20: 31.07.2026 '
                '(предыдущее/начальное); точная дата в источнике неизвестна',
                'test', 1,
            ])
            readings.append([
                'IND-TRUD-PLOT-0003', '2026-08-20', 428,
                'Исходное состояние счётчика: август, текущее; '
                'техническая дата по согласованной схеме граница/20: 20.08.2026 '
                '(текущее); точная дата в источнике неизвестна',
                'test', 1,
            ])

        temp = TemporaryDirectory()
        path = Path(temp.name) / 'package.xlsx'
        book.save(path)
        return temp, path

    def test_dry_run_accepts_month_boundary_before_real_august_reading(self):
        temp, path = self._package()
        self.addCleanup(temp.cleanup)

        call_command('import_water_technical_readings', path)

        self.assertEqual(Reading.objects.count(), 1)
        self.assertFalse(Meter.objects.filter(notes__contains='IND-TRUD-PLOT-0003-H1').exists())

    def test_apply_creates_history_without_overwriting_manual_reading(self):
        temp, path = self._package()
        self.addCleanup(temp.cleanup)

        call_command(
            'import_water_technical_readings', path,
            apply=True, confirm='IMPORT-TECHNICAL-WATER-READINGS',
        )

        self.assertEqual(Reading.objects.filter(meter=self.meter).count(), 3)
        self.assertEqual(Reading.objects.get(meter=self.meter, date='2026-08-01').value, 428)
        historical = Meter.objects.get(notes__contains='ID импорта: IND-TRUD-PLOT-0003-H1')
        self.assertEqual(Reading.objects.filter(meter=historical).count(), 2)

    def test_old_5_20_scheme_is_rejected(self):
        temp, path = self._package(old_scheme=True)
        self.addCleanup(temp.cleanup)

        with self.assertRaises(CommandError):
            call_command('import_water_technical_readings', path)
