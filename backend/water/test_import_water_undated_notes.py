from io import StringIO
import tempfile

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from openpyxl import Workbook

from .models import Account, Meter, SupplyNode


class UndatedWaterNotesImportTests(TestCase):
    def setUp(self):
        self.node = SupplyNode.objects.create(name='Тестовый узел')
        self.account = Account.objects.create(number='TEST-PLOT-001', plot='Участок 1')
        self.meter = Meter.objects.create(
            serial='TEST-METER-001', kind='individual', node=self.node,
            account=self.account, notes='ID импорта: TEST-METER-001',
        )

    def package(self, *, missing_meter=False):
        book = Workbook()
        book.remove(book.active)
        sheets = {
            'Участки': (
                ['plot_id','label','address','cadastral_number','contact_name','phone','source_sheet','source_row','status'],
                [['TEST-PLOT-001','Участок 1','Участок 1','','','','src',1,'готово']],
            ),
            'Люди': (['person_id','full_name','phone','plot_id','role','notes'], []),
            'Узлы': (['node_name','notes'], [['Тестовый узел','']]),
            'Группы': (['group_name','node_name','source','notes'], []),
            'Состав групп': (['plot_id','group_name','starts','ends','notes'], []),
            'Индивидуальные счетчики': (
                ['meter_id','kind','node_name','group_name','plot_id','serial','notes'],
                [['TEST-METER-001','individual','Тестовый узел','','TEST-PLOT-001','TEST-METER-001','']],
            ),
            'Общие и контрольные': (
                ['meter_id','kind','node_name','group_name','serial','source_text','source_sheet','source_row','status'], []
            ),
            'Показания': (
                ['meter_id','date','value_m3','notes','source_sheet','source_row'],
                [[
                    'MISSING-METER' if missing_meter else 'TEST-METER-001', '', 123.45,
                    'Исходное состояние счётчика: июнь; точная дата неизвестна', 'src', 7,
                ]],
            ),
        }
        for name, (header, rows) in sheets.items():
            sheet = book.create_sheet(name)
            sheet.append(header)
            for row in rows:
                sheet.append(row)
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
            path = tmp.name
        book.save(path)
        book.close()
        return path

    def test_dry_run_does_not_write(self):
        path = self.package()
        out = StringIO()

        call_command('import_water_undated_notes', path, stdout=out)

        self.meter.refresh_from_db()
        self.assertNotIn('123.45', self.meter.notes)
        self.assertIn('DRY-RUN', out.getvalue())

    def test_apply_is_idempotent_and_never_creates_reading(self):
        path = self.package()
        for _ in range(2):
            call_command(
                'import_water_undated_notes', path, apply=True,
                confirm='IMPORT-UNDATED-WATER-NOTES', stdout=StringIO(),
            )

        self.meter.refresh_from_db()
        self.assertEqual(self.meter.notes.count('Недатированное исходное показание: 123.45'), 1)
        self.assertEqual(self.meter.reading_set.count(), 0)

    def test_missing_meter_blocks_without_partial_write(self):
        path = self.package(missing_meter=True)

        with self.assertRaises(CommandError):
            call_command(
                'import_water_undated_notes', path, apply=True,
                confirm='IMPORT-UNDATED-WATER-NOTES', stdout=StringIO(),
            )

        self.meter.refresh_from_db()
        self.assertNotIn('123.45', self.meter.notes)
