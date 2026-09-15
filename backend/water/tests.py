from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, Client
from axes.utils import reset

from .models import Account, GroupConsumption, Membership, Meter, Reading, SupplyNode, User, WaterGroup


class WaterTests(TestCase):
    def setUp(self):
        self.node = SupplyNode.objects.create(name='Тестовый узел')
        self.group = WaterGroup.objects.create(name='Тестовая линия', node=self.node)
        self.account = Account.objects.create()
        self.meter = Meter.objects.create(serial='DEMO-1', kind='individual', node=self.node, account=self.account)

    def test_seed_is_repeatable_and_preserves_edits(self):
        call_command('seed_accounts', stdout=StringIO())
        a = Account.objects.get(seed_slot=1)
        a.plot = 'Тестовый участок'
        a.save()
        call_command('seed_accounts', stdout=StringIO())
        a.refresh_from_db()
        self.assertEqual(Account.objects.filter(seed_slot__isnull=False).count(), 305)
        self.assertEqual(a.plot, 'Тестовый участок')
        self.assertIsNone(a.number)

    def test_blank_numbers_and_unique_known_account(self):
        Account.objects.create()
        Account.objects.create(number='0001')
        with self.assertRaises(ValidationError):
            Account.objects.create(number='0001')

    def test_unknown_and_zero_and_decimal_consumption(self):
        first = Reading.objects.create(meter=self.meter, date=date(2026, 7, 1), value=Decimal('100.1'))
        self.assertIsNone(first.consumption)
        same = Reading.objects.create(meter=self.meter, date=date(2026, 8, 1), value=Decimal('100.1'))
        self.assertEqual(same.consumption, 0)
        next_reading = Reading.objects.create(meter=self.meter, date=date(2026, 9, 1), value=Decimal('103.125'))
        self.assertEqual(next_reading.consumption, Decimal('3.025'))

    def test_duplicate_negative_and_out_of_order_rejected(self):
        Reading.objects.create(meter=self.meter, date=date(2026, 8, 1), value=100)
        for day, value in [(date(2026, 8, 1), 110), (date(2026, 9, 1), 90), (date(2026, 7, 1), 110), (date(2026, 7, 1), -1)]:
            with self.subTest(day=day, value=value), self.assertRaises(ValidationError):
                Reading.objects.create(meter=self.meter, date=day, value=value)

    def test_group_volume_does_not_create_individual_readings(self):
        Membership.objects.create(account=self.account, group=self.group, starts=date(2026, 1, 1))
        GroupConsumption.objects.create(group=self.group, starts=date(2026, 8, 1), ends=date(2026, 9, 1), volume=82, reported_by='Тестовый старший')
        self.assertEqual(Reading.objects.count(), 0)
        with self.assertRaises(ValidationError):
            GroupConsumption.objects.create(group=self.group, starts=date(2026, 8, 15), ends=date(2026, 9, 15), volume=20, reported_by='Другой')

    def test_membership_transfer_uses_nonoverlapping_intervals(self):
        Membership.objects.create(account=self.account, group=self.group, starts=date(2026, 1, 1), ends=date(2026, 8, 1))
        with self.assertRaises(ValidationError):
            Membership.objects.create(account=self.account, group=self.group, starts=date(2026, 7, 1))
        Membership.objects.create(account=self.account, group=self.group, starts=date(2026, 8, 1))

    def test_history_and_stale_update(self):
        stale = Account.objects.get(pk=self.account.pk)
        self.account.plot = 'Тестовый адрес'
        self.account._change_reason = 'Уточнение'
        self.account.save()
        self.assertEqual(self.account.history.count(), 2)
        self.assertEqual(self.account.history.first().history_change_reason, 'Уточнение')
        stale.plot = 'Потерянное изменение'
        with self.assertRaises(ValidationError):
            stale.save()

    def test_correction_recalculates_next_interval_and_preserves_history(self):
        first = Reading.objects.create(meter=self.meter, date=date(2026, 7, 1), value=100)
        later = Reading.objects.create(meter=self.meter, date=date(2026, 8, 1), value=120)
        first.value = 105
        first.save()
        self.assertEqual(later.consumption, 15)
        self.assertEqual(first.history.count(), 2)

    def test_meter_with_readings_cannot_move_to_another_account(self):
        Reading.objects.create(meter=self.meter, date=date(2026, 8, 1), value=100)
        self.meter.account = Account.objects.create()
        with self.assertRaises(ValidationError):
            self.meter.save()


class AccessTests(TestCase):
    def setUp(self):
        reset()
        self.admin = User.objects.create_superuser(username='test-admin', password='test-only-long-password')
        self.account = Account.objects.create(plot='Тестовый участок')

    def test_anonymous_and_unprivileged_cannot_read_registry(self):
        self.assertEqual(self.client.get('/admin/water/account/').status_code, 302)
        user = User.objects.create_user(username='test-user', is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get('/admin/water/account/').status_code, 403)
        self.assertEqual(self.client.get(f'/admin/water/account/{self.account.pk}/change/').status_code, 403)

    def test_admin_forms_render_and_save_with_attribution(self):
        self.client.force_login(self.admin)
        for model in ['account', 'supplynode', 'watergroup', 'membership', 'meter', 'reading', 'groupconsumption']:
            self.assertEqual(self.client.get(f'/admin/water/{model}/').status_code, 200)
            self.assertEqual(self.client.get(f'/admin/water/{model}/add/').status_code, 200)
        url = f'/admin/water/account/{self.account.pk}/change/'
        response = self.client.post(url, {'version': self.account.version, 'plot': 'Новый адрес', 'change_reason': 'Сверка'})
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertEqual(self.account.plot, 'Новый адрес')
        self.assertEqual(self.account.history.first().history_user, self.admin)

    def test_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post('/admin/water/account/add/', {'plot': 'Тест'}).status_code, 403)

    def test_export_neutralizes_spreadsheet_formulas(self):
        self.account.plot = '=1+1'
        self.account.save()
        self.client.force_login(self.admin)
        response = self.client.post('/admin/water/account/', {
            'action': 'export_accounts', '_selected_action': [self.account.pk],
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("'=1+1", response.content.decode('utf-8-sig'))

    def test_edit_requires_reason_and_delete_is_forbidden(self):
        self.client.force_login(self.admin)
        response = self.client.post(f'/admin/water/account/{self.account.pk}/change/', {
            'version': self.account.version, 'plot': 'Без причины',
        })
        self.assertEqual(response.status_code, 200)
        self.account.refresh_from_db()
        self.assertEqual(self.account.plot, 'Тестовый участок')
        self.assertEqual(self.client.post(f'/admin/water/account/{self.account.pk}/delete/', {'post': 'yes'}).status_code, 403)

    def test_password_guessing_locks_login(self):
        for _ in range(5):
            self.client.post('/admin/login/', {'username': 'test-admin', 'password': 'wrong'})
        result = self.client.post('/admin/login/', {'username': 'test-admin', 'password': 'test-only-long-password'})
        self.assertEqual(result.status_code, 429)
        self.assertNotIn('_auth_user_id', self.client.session)
