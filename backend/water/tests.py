from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, Client
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from axes.utils import reset

from .models import Account, GroupConsumption, LandPlot, Membership, Meter, Person, PlotRelation, Reading, SupplyNode, User, WaterGroup


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


class MFAAccessMixin:
    """Give a test client the same verified session created after real OTP login."""

    def login_as(self, user, client=None):
        client = client or self.client
        device, _ = TOTPDevice.objects.get_or_create(user=user, defaults={'name': 'test device'})
        client.force_login(user)
        session = client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        return client


class AccessTests(MFAAccessMixin, TestCase):
    def setUp(self):
        reset()
        self.admin = User.objects.create_superuser(username='test-admin', password='test-only-long-password')
        self.account = Account.objects.create(plot='Тестовый участок')

    def test_anonymous_and_unprivileged_cannot_read_registry(self):
        self.assertEqual(self.client.get('/admin/water/account/').status_code, 302)
        user = User.objects.create_user(username='test-user', is_staff=True)
        self.login_as(user)
        self.assertEqual(self.client.get('/admin/water/account/').status_code, 403)
        self.assertEqual(self.client.get(f'/admin/water/account/{self.account.pk}/change/').status_code, 403)

    def test_admin_forms_render_and_save_with_attribution(self):
        self.login_as(self.admin)
        for model in ['account', 'supplynode', 'watergroup', 'membership', 'meter', 'reading', 'groupconsumption']:
            self.assertEqual(self.client.get(f'/admin/water/{model}/').status_code, 200)
            self.assertEqual(self.client.get(f'/admin/water/{model}/add/').status_code, 200)
        url = f'/admin/water/account/{self.account.pk}/change/'
        response = self.client.post(url, {'version': self.account.version, 'plot': 'Новый адрес', 'change_reason': 'Сверка'})
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.assertEqual(self.account.plot, 'Новый адрес')
        self.assertEqual(self.account.history.first().history_user, self.admin)

    def test_account_shows_latest_group_total_without_individual_reading(self):
        node = SupplyNode.objects.create(name='Узел группового теста')
        group = WaterGroup.objects.create(name='Группа Матвеева', node=node, source='reported')
        Membership.objects.create(account=self.account, group=group, starts=date(2020, 1, 1))
        GroupConsumption.objects.create(
            group=group,
            starts=date(2026, 9, 1),
            ends=date(2026, 10, 1),
            volume=Decimal('111'),
            reported_by='Матвеев',
        )
        self.login_as(self.admin)

        response = self.client.get(f'/admin/water/account/{self.account.pk}/change/')

        self.assertContains(response, 'Последние кубы текущей группы')
        self.assertContains(response, 'Группа Матвеева: 111.000 м³ за 01.09.2026–01.10.2026')
        self.assertEqual(Reading.objects.filter(meter__account=self.account).count(), 0)

    def test_csrf_required(self):
        client = Client(enforce_csrf_checks=True)
        self.login_as(self.admin, client)
        self.assertEqual(client.post('/admin/water/account/add/', {'plot': 'Тест'}).status_code, 403)

    def test_export_neutralizes_spreadsheet_formulas(self):
        self.account.plot = '=1+1'
        self.account.save()
        self.login_as(self.admin)
        response = self.client.post('/admin/water/account/', {
            'action': 'export_accounts', '_selected_action': [self.account.pk],
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn("'=1+1", response.content.decode('utf-8-sig'))

    def test_edit_requires_reason_and_delete_is_forbidden(self):
        self.login_as(self.admin)
        response = self.client.post(f'/admin/water/account/{self.account.pk}/change/', {
            'version': self.account.version, 'plot': 'Без причины',
        })
        self.assertEqual(response.status_code, 200)
        self.account.refresh_from_db()
        self.assertEqual(self.account.plot, 'Тестовый участок')
        self.assertEqual(self.client.post(f'/admin/water/account/{self.account.pk}/delete/', {'post': 'yes'}).status_code, 403)

    def test_password_only_cannot_open_admin_and_first_login_starts_setup(self):
        self.client.force_login(self.admin)
        result = self.client.get('/admin/water/account/')
        self.assertEqual(result.status_code, 302)
        self.assertIn('/admin/login/', result['Location'])

        result = self.client.get('/admin/login/')
        self.assertEqual(result.status_code, 302)
        self.assertIn('/admin/account/login/', result['Location'])

    def test_password_guessing_locks_login(self):
        for _ in range(5):
            self.client.post('/admin/account/login/', {
                'login_view-current_step': 'auth', 'auth-username': 'test-admin', 'auth-password': 'wrong',
            })
        result = self.client.post('/admin/account/login/', {
            'login_view-current_step': 'auth', 'auth-username': 'test-admin', 'auth-password': 'test-only-long-password',
        })
        self.assertEqual(result.status_code, 429)
        self.assertNotIn('_auth_user_id', self.client.session)



class RoleAuditTests(MFAAccessMixin, TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        call_command('setup_roles', stdout=StringIO())
        self.operator = User.objects.create_user(username='operator-test', is_staff=True)
        self.operator.groups.add(Group.objects.get(name='Оператор воды'))
        self.manager = User.objects.create_user(username='manager-test', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Администратор СНТ'))
        self.account = Account.objects.create(plot='Тест')
        self.node = SupplyNode.objects.create(name='Тестовый узел')
        self.meter = Meter.objects.create(serial='TEST-1', kind='individual', node=self.node, account=self.account)

    def test_operator_can_add_but_not_correct_or_escalate_or_export(self):
        from django.contrib.admin.models import LogEntry
        self.login_as(self.operator)
        response = self.client.post('/admin/water/reading/add/', {
            'meter': self.meter.pk, 'date': '2026-09-01', 'value': '100', 'version': 0,
        })
        self.assertEqual(response.status_code, 302)
        reading = Reading.objects.get()
        self.assertEqual(reading.history.first().history_user, self.operator)
        self.assertTrue(LogEntry.objects.filter(user=self.operator, object_id=str(reading.pk), content_type__model='reading').exists())
        response = self.client.post(f'/admin/water/reading/{reading.pk}/change/', {
            'meter': self.meter.pk, 'date': '2026-09-01', 'value': '101', 'version': reading.version, 'change_reason': 'Обход',
        })
        self.assertEqual(response.status_code, 403)
        reading.refresh_from_db()
        self.assertEqual(reading.value, 100)
        for path in ['/admin/water/account/add/', '/admin/water/user/', '/admin/auth/group/', '/admin/admin/logentry/']:
            self.assertEqual(self.client.get(path).status_code, 403, path)
        response = self.client.post('/admin/water/account/', {'action': 'export_accounts', '_selected_action': [self.account.pk]})
        self.assertNotIn('text/csv', response.get('Content-Type', ''))
        self.assertFalse(self.operator.has_perm('water.delete_reading'))

    def test_manager_correction_reason_history_and_readonly_journal(self):
        from django.contrib.admin.models import LogEntry
        self.login_as(self.manager)
        url = f'/admin/water/account/{self.account.pk}/change/'
        response = self.client.post(url, {'plot': 'Исправлено', 'version': self.account.version, 'change_reason': 'Сверка с документом'})
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        record = self.account.history.first()
        self.assertEqual(record.history_user, self.manager)
        self.assertEqual(record.history_change_reason, 'Сверка с документом')
        self.assertEqual(record.prev_record.plot, 'Тест')
        entry = LogEntry.objects.filter(user=self.manager).latest('action_time')
        self.assertIn('Сверка с документом', entry.get_change_message())
        self.assertEqual(self.client.get('/admin/admin/logentry/').status_code, 200)
        self.assertEqual(self.client.post(f'/admin/admin/logentry/{entry.pk}/change/', {}).status_code, 403)
        self.assertEqual(self.client.post(f'/admin/admin/logentry/{entry.pk}/delete/', {}).status_code, 403)
        for path in ['/admin/water/user/', '/admin/auth/group/']:
            self.assertEqual(self.client.get(path).status_code, 403)
        response = self.client.get(url)
        self.assertContains(response, 'manager-test')
        self.assertContains(response, 'Автор не указан')
        self.assertEqual(self.client.get(f'/admin/water/account/{self.account.pk}/history/').status_code, 200)

    def test_role_sync_preserves_users_and_revokes_extra_group_grants(self):
        from django.contrib.auth.models import Permission
        group = self.operator.groups.get()
        group.permissions.add(Permission.objects.get(codename='change_user', content_type__app_label='water'))
        call_command('setup_roles', stdout=StringIO())
        fresh = User.objects.get(pk=self.operator.pk)
        self.assertFalse(fresh.has_perm('water.change_user'))
        self.assertTrue(fresh.has_perm('water.add_reading'))
        self.assertEqual(fresh.groups.count(), 1)

    def test_disabled_operator_loses_existing_session(self):
        self.login_as(self.operator)
        self.operator.is_active = False
        self.operator.save()
        self.assertEqual(self.client.get('/admin/water/account/').status_code, 302)

    def test_manager_export_logged_and_delete_forbidden(self):
        from django.contrib.admin.models import LogEntry
        self.login_as(self.manager)
        response = self.client.post('/admin/water/account/', {'action': 'export_accounts', '_selected_action': [self.account.pk]})
        self.assertIn('text/csv', response['Content-Type'])
        self.assertTrue(LogEntry.objects.filter(user=self.manager, change_message='Экспорт CSV: 1 записей').exists())
        self.assertEqual(self.client.post(f'/admin/water/account/{self.account.pk}/delete/', {}).status_code, 403)

    def test_history_post_cannot_revert_even_for_superuser(self):
        from django.urls import reverse
        superuser = User.objects.create_superuser(username='technical', password='test-only-password')
        for user in (self.operator, self.manager, superuser):
            self.login_as(user)
            history = self.account.history.first()
            url = reverse('admin:water_account_simple_history', args=[self.account.pk, history.history_id])
            self.assertEqual(self.client.get(url).status_code, 200)
            self.assertEqual(self.client.post(url, {'version': self.account.version, 'plot': 'Обход', 'change_reason': 'Обход'}).status_code, 403)
        self.account.refresh_from_db()
        self.assertEqual(self.account.plot, 'Тест')


class RegistryTests(MFAAccessMixin, TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        call_command('setup_roles', stdout=StringIO())
        self.manager = User.objects.create_user(username='registry-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Администратор СНТ'))
        self.operator = User.objects.create_user(username='water-operator', is_staff=True)
        self.operator.groups.add(Group.objects.get(name='Оператор воды'))
        self.account = Account.objects.create(number='001')
        self.plot = LandPlot.objects.create(label='Участок 12', account=self.account)
        self.person = Person.objects.create(full_name='Иванов Иван Иванович')

    def test_person_plot_account_are_separate_and_history_is_preserved(self):
        other = LandPlot.objects.create(label='Участок 13', account=self.account)
        owner = PlotRelation.objects.create(person=self.person, plot=self.plot, role='owner', starts=date(2020, 1, 1))
        PlotRelation.objects.create(person=self.person, plot=other, role='owner', starts=date(2020, 1, 1))
        with self.assertRaises(ValidationError):
            PlotRelation.objects.create(person=self.person, plot=self.plot, role='owner', starts=date(2021, 1, 1))
        PlotRelation.objects.create(person=self.person, plot=self.plot, role='representative', starts=date(2021, 1, 1))
        owner.ends = date(2022, 1, 1)
        owner.save()
        new_owner = Person.objects.create(full_name='Петров Пётр Петрович')
        PlotRelation.objects.create(person=new_owner, plot=self.plot, role='owner', starts=date(2022, 1, 1))
        self.assertEqual(self.account.land_plots.count(), 2)
        self.assertEqual(self.plot.relations.count(), 3)
        self.assertEqual(owner.history.count(), 2)

    def test_invalid_relation_dates_and_deletion_are_rejected(self):
        with self.assertRaises(ValidationError):
            PlotRelation.objects.create(person=self.person, plot=self.plot, role='owner', starts=date(2025, 1, 2), ends=date(2025, 1, 2))
        owner = PlotRelation.objects.create(person=self.person, plot=self.plot, role='owner', starts=date(2025, 1, 1))
        with self.assertRaises(Exception):
            self.person.delete()
        with self.assertRaises(Exception):
            self.plot.delete()
        self.assertEqual(PlotRelation.objects.get(pk=owner.pk), owner)

    def test_manager_can_register_relations_with_actor_and_operator_cannot_view(self):
        self.login_as(self.manager)
        response = self.client.post('/admin/water/plotrelation/add/', {
            'person': self.person.pk, 'plot': self.plot.pk, 'role': 'owner',
            'starts': '2025-01-01', 'version': 0,
        })
        self.assertEqual(response.status_code, 302)
        relation = PlotRelation.objects.get()
        self.assertEqual(relation.history.first().history_user, self.manager)
        self.assertEqual(self.client.get('/admin/water/person/').status_code, 200)
        self.assertEqual(self.client.get('/admin/water/landplot/').status_code, 200)
        self.assertEqual(self.client.get('/admin/water/plotrelation/').status_code, 200)
        self.login_as(self.operator)
        for url in ['/admin/water/person/', '/admin/water/landplot/', '/admin/water/plotrelation/']:
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_setup_roles_does_not_grant_registry_to_operator(self):
        self.assertTrue(self.manager.has_perm('water.change_person'))
        self.assertTrue(self.manager.has_perm('water.view_historicalplotrelation'))
        self.assertFalse(self.operator.has_perm('water.view_person'))
        self.assertFalse(self.operator.has_perm('water.add_landplot'))


class MFARecoveryTests(MFAAccessMixin, TestCase):
    def test_reset_mfa_removes_devices_and_revokes_sessions(self):
        user = User.objects.create_user(username='lost-phone', password='test-only-long-password', is_staff=True)
        self.login_as(user)
        recovery = StaticDevice.objects.create(user=user, name='backup')
        StaticToken.objects.create(device=recovery, token='safe-token')
        self.assertTrue(self.client.session.session_key)

        call_command('reset_mfa', user.username, '--yes', stdout=StringIO())

        self.assertFalse(TOTPDevice.objects.filter(user=user).exists())
        self.assertFalse(StaticDevice.objects.filter(user=user).exists())
        self.assertFalse(self.client.session.exists(self.client.session.session_key))

    def test_reset_mfa_requires_explicit_confirmation(self):
        user = User.objects.create_user(username='confirmation', is_staff=True)
        with self.assertRaises(Exception):
            call_command('reset_mfa', user.username, stdout=StringIO())
