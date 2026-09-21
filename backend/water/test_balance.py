from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase

from .balance import WaterBalanceForm, calculate_water_balance
from .models import Account, GroupConsumption, Membership, Meter, Reading, SupplyNode, WaterGroup


class WaterBalanceTests(TestCase):
    starts = date(2026, 1, 1)
    ends = date(2026, 2, 1)

    def meter(self, *, node, serial, kind, group=None, account=None):
        return Meter.objects.create(
            node=node, serial=serial, kind=kind, group=group, account=account,
        )

    def readings(self, meter, first, last):
        Reading.objects.create(meter=meter, date=self.starts, value=Decimal(first))
        Reading.objects.create(meter=meter, date=self.ends, value=Decimal(last))

    def test_complete_balance_uses_configured_sources_without_distribution(self):
        node = SupplyNode.objects.create(name='Скважина 1')
        main = self.meter(node=node, serial='MAIN', kind='main')
        self.readings(main, '100', '200')

        reported_group = WaterGroup.objects.create(name='Линия А', node=node, source='reported')
        GroupConsumption.objects.create(
            group=reported_group, starts=self.starts, ends=self.ends,
            volume=Decimal('30'), reported_by='Старший А',
        )

        metered_group = WaterGroup.objects.create(name='Линия Б', node=node, source='meter')
        line = self.meter(node=node, serial='LINE-B', kind='line', group=metered_group)
        self.readings(line, '10', '30')

        individual_group = WaterGroup.objects.create(name='Линия В', node=node, source='individual')
        account = Account.objects.create(number='101', plot='ул. Тестовая, 1')
        Membership.objects.create(account=account, group=individual_group, starts=date(2025, 1, 1))
        individual = self.meter(node=node, serial='IND-101', kind='individual', account=account)
        self.readings(individual, '5', '30')

        irrigation = self.meter(node=node, serial='POLIV', kind='irrigation')
        self.readings(irrigation, '1', '6')

        report = calculate_water_balance(self.starts, self.ends)
        self.assertTrue(report.complete)
        self.assertEqual(report.input_volume, Decimal('100'))
        self.assertEqual(report.confirmed_volume, Decimal('80'))
        self.assertEqual(report.loss_volume, Decimal('20'))
        self.assertEqual(report.loss_percent, Decimal('20.00'))
        self.assertEqual(len(report.nodes[0].group_lines), 3)
        self.assertEqual(report.nodes[0].other_lines[0].volume, Decimal('5'))

    def test_missing_exact_boundary_never_uses_nearby_reading(self):
        node = SupplyNode.objects.create(name='Скважина 1')
        main = self.meter(node=node, serial='MAIN', kind='main')
        self.readings(main, '100', '150')

        group = WaterGroup.objects.create(name='Линия А', node=node, source='meter')
        line = self.meter(node=node, serial='LINE-A', kind='line', group=group)
        Reading.objects.create(meter=line, date=self.starts - timedelta(days=1), value=Decimal('10'))
        Reading.objects.create(meter=line, date=self.ends + timedelta(days=1), value=Decimal('40'))

        report = calculate_water_balance(self.starts, self.ends)
        node_result = report.nodes[0]
        self.assertFalse(report.complete)
        self.assertFalse(node_result.complete)
        self.assertIsNone(node_result.loss_volume)
        self.assertIsNone(node_result.group_lines[0].volume)
        self.assertIn('Нет граничного показания', node_result.group_lines[0].detail)

    def test_unknown_group_source_marks_report_incomplete(self):
        node = SupplyNode.objects.create(name='Скважина 1')
        main = self.meter(node=node, serial='MAIN', kind='main')
        self.readings(main, '0', '10')
        WaterGroup.objects.create(name='Не настроено', node=node, source='unknown')

        report = calculate_water_balance(self.starts, self.ends)
        self.assertFalse(report.complete)
        self.assertIsNone(report.loss_volume)
        self.assertIn('Источник не определён', report.nodes[0].group_lines[0].source)

    def test_individual_group_is_incomplete_if_one_member_has_no_boundary_data(self):
        node = SupplyNode.objects.create(name='Скважина 1')
        main = self.meter(node=node, serial='MAIN', kind='main')
        self.readings(main, '0', '100')
        group = WaterGroup.objects.create(name='Индивидуальная группа', node=node, source='individual')

        ok_account = Account.objects.create(number='1', plot='Участок 1')
        missing_account = Account.objects.create(number='2', plot='Участок 2')
        Membership.objects.create(account=ok_account, group=group, starts=date(2025, 1, 1))
        Membership.objects.create(account=missing_account, group=group, starts=date(2025, 1, 1))
        ok_meter = self.meter(node=node, serial='IND-1', kind='individual', account=ok_account)
        missing_meter = self.meter(node=node, serial='IND-2', kind='individual', account=missing_account)
        self.readings(ok_meter, '0', '20')
        Reading.objects.create(meter=missing_meter, date=self.starts, value=Decimal('0'))

        report = calculate_water_balance(self.starts, self.ends)
        line = report.nodes[0].group_lines[0]
        self.assertFalse(line.complete)
        self.assertIsNone(line.volume)
        self.assertIn('Нет граничного показания', line.detail)

    def test_period_form_rejects_zero_or_negative_period(self):
        form = WaterBalanceForm({'starts': '2026-02-01', 'ends': '2026-02-01'})
        self.assertFalse(form.is_valid())
        self.assertIn('ends', form.errors)
