from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
import tempfile

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, Client, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice
from axes.utils import reset

from .billing import account_totals, allocate_payment, calculate_period
from .imports import apply_import_row, stage_import
from .portal import issue_invite, issue_password_reset
from .models import (
    Account, AccountDocument, AppealCategory, BillingAssignment, BillingPeriod, BillingPolicy, Charge,
    ControllerReadingSubmission, DocumentCategory,
    GroupConsumption, ImportBatch, ImportRow, LandPlot, Membership, Meter,
    Payment, PaymentAllocation, Person, PlotRelation, Reading, ResidentAccess,
    ResidentAppeal, ResidentAppealMessage, ResidentInvite, ResidentPasswordReset,
    SupplyNode, Tariff, User, WaterGroup,
)


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

    def test_meter_lifecycle_and_future_reading_are_validated(self):
        today = timezone.localdate()
        self.meter.commissioned_on = today - timedelta(days=10)
        self.meter.seal_number = 'PL-77'
        self.meter.retired_on = today - timedelta(days=11)
        with self.assertRaises(ValidationError):
            self.meter.save()
        self.meter.retired_on = None
        self.meter.save()
        with self.assertRaises(ValidationError):
            Reading.objects.create(meter=self.meter, date=today - timedelta(days=11), value=1)
        with self.assertRaises(ValidationError):
            Reading.objects.create(meter=self.meter, date=today + timedelta(days=1), value=1)


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
        self.manager.groups.add(Group.objects.get(name='Администратор ТСН'))
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

    def test_legacy_admin_role_is_renamed_without_losing_members(self):
        from django.contrib.auth.models import Group
        legacy = Group.objects.create(name='Администратор СНТ')
        legacy_user = User.objects.create_user(username='legacy-manager', is_staff=True)
        legacy_user.groups.add(legacy)

        call_command('setup_roles', stdout=StringIO())

        legacy_user.refresh_from_db()
        self.assertFalse(Group.objects.filter(name='Администратор СНТ').exists())
        self.assertTrue(legacy_user.groups.filter(name='Администратор ТСН').exists())
        self.assertTrue(legacy_user.has_perm('water.change_person'))

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

    def test_operator_workspace_batch_is_atomic_and_attributed(self):
        from django.contrib.admin.models import LogEntry
        other = Meter.objects.create(
            serial='TEST-2', kind='individual', node=self.node,
            account=Account.objects.create(plot='Второй участок'),
        )
        selected = timezone.localdate() - timedelta(days=1)
        previous = selected - timedelta(days=10)
        Reading.objects.create(meter=self.meter, date=previous, value=100)
        csrf_client = Client(enforce_csrf_checks=True)
        self.login_as(self.operator, csrf_client)
        self.assertEqual(csrf_client.post('/admin/water/reading/workspace/', {
            'date': selected.isoformat(), f'value_{self.meter.pk}': '110',
        }).status_code, 403)
        self.login_as(self.operator)

        url = '/admin/water/reading/workspace/'
        response = self.client.get(url, {'date': selected.isoformat(), 'status': 'missing'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Рабочее место оператора воды')
        self.assertContains(response, 'TEST-1')
        self.assertContains(response, 'TEST-2')

        response = self.client.post(url, {
            'date': selected.isoformat(), f'value_{self.meter.pk}': '110.250',
            f'notes_{self.meter.pk}': 'Обход', f'value_{other.pk}': '50',
        })
        self.assertEqual(response.status_code, 302)
        saved = Reading.objects.get(meter=self.meter, date=selected)
        self.assertEqual(saved.value, Decimal('110.250'))
        self.assertEqual(saved.history.first().history_user, self.operator)
        self.assertEqual(saved.history.first().history_change_reason, 'Пакетный ввод показаний')
        self.assertEqual(LogEntry.objects.filter(user=self.operator, content_type__model='reading', object_id=str(saved.pk)).count(), 1)

        next_date = timezone.localdate()
        response = self.client.post(url, {
            'date': next_date.isoformat(), f'value_{self.meter.pk}': '109',
            f'value_{other.pk}': '60',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Показание нарушает последовательность')
        self.assertFalse(Reading.objects.filter(date=next_date).exists())

    def test_workspace_export_is_manager_only_and_logged(self):
        from django.contrib.admin.models import LogEntry
        selected = timezone.localdate() - timedelta(days=1)
        Reading.objects.create(meter=self.meter, date=selected, value=Decimal('25'))
        url = '/admin/water/reading/workspace/'

        self.login_as(self.operator)
        self.assertEqual(self.client.get(url, {'date': selected.isoformat(), 'export': 'csv'}).status_code, 403)

        self.login_as(self.manager)
        response = self.client.get(url, {'date': selected.isoformat(), 'export': 'csv'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        report = response.content.decode('utf-8-sig')
        self.assertIn('TEST-1', report)
        self.assertNotIn('Телефон', report)
        self.assertTrue(LogEntry.objects.filter(user=self.manager, object_repr='Отчёт рабочего места').exists())

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
        self.manager.groups.add(Group.objects.get(name='Администратор ТСН'))
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


class FlexibleBillingTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='B-1', plot='Первый')
        self.other = Account.objects.create(number='B-2', plot='Второй')
        self.node = SupplyNode.objects.create(name='Финансовый узел')
        self.group = WaterGroup.objects.create(name='Финансовая группа', node=self.node)
        self.period = BillingPeriod.objects.create(starts=date(2026, 9, 1), ends=date(2026, 10, 1))

    def test_policies_cover_supported_variants_and_assign_by_scope(self):
        policy = BillingPolicy.objects.create(
            name='Индивидуальное правило', missing_reading='norm', monthly_norm_m3=Decimal('7.500'),
            loss_distribution='volume', rounding='up_ruble', payment_allocation='reference',
        )
        account_rule = BillingAssignment.objects.create(
            policy=policy, account=self.account, starts=date(2026, 1, 1), priority=200,
        )
        group_rule = BillingAssignment.objects.create(
            policy=policy, group=self.group, starts=date(2026, 1, 1), priority=100,
        )
        self.assertEqual(account_rule.account, self.account)
        self.assertEqual(group_rule.group, self.group)
        with self.assertRaises(ValidationError):
            BillingAssignment.objects.create(
                policy=policy, account=self.account, group=self.group, starts=date(2026, 1, 1),
            )

    def test_tariffs_can_be_global_group_or_account_but_not_overlap(self):
        Tariff.objects.create(name='Общий', rate=Decimal('40'), starts=date(2026, 1, 1), ends=date(2026, 7, 1))
        Tariff.objects.create(name='Общий новый', rate=Decimal('45'), starts=date(2026, 7, 1))
        Tariff.objects.create(name='Для группы', rate=Decimal('42'), starts=date(2026, 1, 1), group=self.group)
        Tariff.objects.create(name='Льготный', rate=Decimal('30'), starts=date(2026, 1, 1), account=self.account)
        with self.assertRaises(ValidationError):
            Tariff.objects.create(name='Пересечение', rate=Decimal('41'), starts=date(2026, 6, 1))
        with self.assertRaises(ValidationError):
            Tariff.objects.create(
                name='Двойная область', rate=Decimal('1'), starts=date(2026, 1, 1),
                account=self.account, group=self.group,
            )

    def test_charge_math_and_approved_records_are_immutable(self):
        charge = Charge.objects.create(
            account=self.account, period=self.period, kind='water', volume=Decimal('3.000'),
            rate=Decimal('40.0000'), amount=Decimal('120.00'), status='approved',
        )
        charge.amount = Decimal('121.00')
        with self.assertRaises(ValidationError):
            charge.save()
        with self.assertRaises(ValidationError):
            Charge.objects.create(
                account=self.account, period=self.period, kind='water', volume=Decimal('3.000'),
                rate=Decimal('40.0000'), amount=Decimal('-1.00'),
            )

    def test_payment_allocation_cannot_cross_accounts_or_exceed_payment(self):
        charge = Charge.objects.create(
            account=self.account, period=self.period, kind='service', amount=Decimal('100.00'), status='approved',
        )
        other_charge = Charge.objects.create(
            account=self.other, period=self.period, kind='service', amount=Decimal('100.00'), status='approved',
        )
        second_charge = Charge.objects.create(
            account=self.account, period=self.period, kind='adjustment', amount=Decimal('50.00'), status='approved',
        )
        payment = Payment.objects.create(
            account=self.account, paid_on=date(2026, 9, 15), amount=Decimal('80.00'), method='bank', status='confirmed',
        )
        PaymentAllocation.objects.create(payment=payment, charge=charge, amount=Decimal('60.00'))
        with self.assertRaises(ValidationError):
            PaymentAllocation.objects.create(payment=payment, charge=other_charge, amount=Decimal('10.00'))
        with self.assertRaises(ValidationError):
            PaymentAllocation.objects.create(payment=payment, charge=second_charge, amount=Decimal('30.00'))

    def test_finance_permissions_belong_to_manager_not_operator(self):
        from django.contrib.auth.models import Group
        call_command('setup_roles', stdout=StringIO())
        manager = User.objects.create_user(username='finance-manager')
        manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        operator = User.objects.create_user(username='finance-operator')
        operator.groups.add(Group.objects.get(name='Оператор воды'))
        self.assertTrue(manager.has_perm('water.add_tariff'))
        self.assertTrue(manager.has_perm('water.change_payment'))
        self.assertFalse(operator.has_perm('water.view_payment'))


class BillingCalculationTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='CALC-1', plot='Расчётный')
        self.node = SupplyNode.objects.create(name='Расчётный узел')
        self.group = WaterGroup.objects.create(name='Расчётная группа', node=self.node)
        Membership.objects.create(account=self.account, group=self.group, starts=date(2026, 1, 1))
        self.meter = Meter.objects.create(
            serial='CALC-METER', kind='individual', node=self.node, account=self.account,
            commissioned_on=date(2026, 1, 1),
        )
        self.period = BillingPeriod.objects.create(starts=date(2026, 7, 1), ends=date(2026, 8, 1))
        self.default_policy = BillingPolicy.objects.create(
            name='Общие безопасные правила', is_default=True, missing_reading='draft', rounding='kopeck',
        )
        Tariff.objects.create(name='Общий тариф', rate=Decimal('40'), starts=date(2026, 1, 1))

    def test_actual_consumption_creates_idempotent_draft_with_explanation(self):
        Reading.objects.create(meter=self.meter, date=self.period.starts, value=Decimal('100'))
        Reading.objects.create(meter=self.meter, date=self.period.ends, value=Decimal('112.500'))

        result = calculate_period(self.period)

        self.assertEqual(result[0].outcome, 'created')
        charge = Charge.objects.get()
        self.assertEqual(charge.volume, Decimal('12.500'))
        self.assertEqual(charge.amount, Decimal('500.00'))
        self.assertEqual(charge.origin, 'calculation')
        self.assertIn('Общий тариф', charge.calculation)
        result = calculate_period(self.period)
        self.assertEqual(result[0].outcome, 'updated')
        self.assertEqual(Charge.objects.count(), 1)

    def test_account_tariff_and_group_policy_override_defaults(self):
        group_policy = BillingPolicy.objects.create(
            name='Групповой норматив', missing_reading='norm', monthly_norm_m3=Decimal('7.000'),
            rounding='up_ruble',
        )
        BillingAssignment.objects.create(
            policy=group_policy, group=self.group, starts=date(2026, 1, 1),
        )
        Tariff.objects.create(
            name='Индивидуальный тариф', rate=Decimal('30.0100'), starts=date(2026, 1, 1), account=self.account,
        )

        result = calculate_period(self.period)

        charge = result[0].charge
        self.assertEqual(charge.kind, 'norm')
        self.assertEqual(charge.volume, Decimal('7.000'))
        self.assertEqual(charge.amount, Decimal('211.00'))
        self.assertIn('Индивидуальный тариф', charge.calculation)
        self.assertIn('Групповой норматив', charge.calculation)

    def test_missing_safe_rule_leaves_account_for_review_without_charge(self):
        results = calculate_period(self.period)
        self.assertEqual(results[0].outcome, 'review')
        self.assertFalse(Charge.objects.exists())
        self.assertEqual(BillingPeriod.objects.get(pk=self.period.pk).status, 'calculated')

    def test_historical_average_uses_only_readings_before_period(self):
        self.default_policy.missing_reading = 'average'
        self.default_policy.average_periods = 2
        self.default_policy.save()
        Reading.objects.create(meter=self.meter, date=date(2026, 4, 1), value=Decimal('10'))
        Reading.objects.create(meter=self.meter, date=date(2026, 5, 1), value=Decimal('20'))
        Reading.objects.create(meter=self.meter, date=date(2026, 6, 1), value=Decimal('40'))
        Reading.objects.create(meter=self.meter, date=date(2026, 9, 1), value=Decimal('1000'))

        charge = calculate_period(self.period)[0].charge

        self.assertEqual(charge.volume, Decimal('15.000'))
        self.assertEqual(charge.amount, Decimal('600.00'))

    def test_approved_period_and_approved_auto_charge_are_not_overwritten(self):
        Reading.objects.create(meter=self.meter, date=self.period.starts, value=Decimal('100'))
        Reading.objects.create(meter=self.meter, date=self.period.ends, value=Decimal('110'))
        charge = calculate_period(self.period)[0].charge
        charge.status = 'approved'
        charge.save()
        self.period.refresh_from_db()
        self.period.status = 'approved'
        self.period.save()
        with self.assertRaises(ValidationError):
            calculate_period(self.period)

    def test_only_one_default_policy_is_allowed(self):
        with self.assertRaises(ValidationError):
            BillingPolicy.objects.create(name='Другие общие правила', is_default=True)


class LossDistributionTests(TestCase):
    def setUp(self):
        self.node = SupplyNode.objects.create(name='Узел потерь')
        self.group = WaterGroup.objects.create(name='Группа потерь', node=self.node, source='reported')
        self.accounts = [
            Account.objects.create(number='LOSS-1'),
            Account.objects.create(number='LOSS-2'),
        ]
        self.period = BillingPeriod.objects.create(starts=date(2026, 7, 1), ends=date(2026, 8, 1))
        self.policy = BillingPolicy.objects.create(
            name='Потери поровну', is_default=True, missing_reading='draft',
            loss_distribution='equal_account',
        )
        Tariff.objects.create(name='Тариф потерь', rate=Decimal('10'), starts=date(2026, 1, 1))
        for index, account in enumerate(self.accounts, 1):
            Membership.objects.create(account=account, group=self.group, starts=date(2026, 1, 1))
            meter = Meter.objects.create(
                serial=f'LOSS-{index}', kind='individual', node=self.node, account=account,
                commissioned_on=date(2026, 1, 1),
            )
            Reading.objects.create(meter=meter, date=self.period.starts, value=Decimal('100'))
            Reading.objects.create(meter=meter, date=self.period.ends, value=Decimal('110'))

    def test_reported_loss_is_distributed_equally_without_duplicates(self):
        GroupConsumption.objects.create(
            group=self.group, starts=self.period.starts, ends=self.period.ends,
            volume=Decimal('30'), reported_by='Старший',
        )
        results = calculate_period(self.period)
        losses = Charge.objects.filter(kind='loss').order_by('account_id')
        self.assertEqual(losses.count(), 2)
        self.assertEqual([item.volume for item in losses], [Decimal('5.000'), Decimal('5.000')])
        self.assertEqual([item.amount for item in losses], [Decimal('50.00'), Decimal('50.00')])
        self.assertTrue(any('потери 10.000' in result.message for result in results))
        calculate_period(self.period)
        self.assertEqual(Charge.objects.filter(kind='loss').count(), 2)

    def test_area_distribution_requires_area_and_uses_plot_area(self):
        self.policy.loss_distribution = 'area'
        self.policy.save()
        LandPlot.objects.create(label='Малый', account=self.accounts[0], area_m2=Decimal('100'))
        LandPlot.objects.create(label='Большой', account=self.accounts[1], area_m2=Decimal('300'))
        GroupConsumption.objects.create(
            group=self.group, starts=self.period.starts, ends=self.period.ends,
            volume=Decimal('24'), reported_by='Старший',
        )
        calculate_period(self.period)
        losses = Charge.objects.filter(kind='loss').order_by('account_id')
        self.assertEqual([item.volume for item in losses], [Decimal('1.000'), Decimal('3.000')])

    def test_negative_or_unverifiable_loss_is_left_for_review(self):
        GroupConsumption.objects.create(
            group=self.group, starts=self.period.starts, ends=self.period.ends,
            volume=Decimal('15'), reported_by='Старший',
        )
        results = calculate_period(self.period)
        self.assertFalse(Charge.objects.filter(kind='loss').exists())
        self.assertTrue(any(result.outcome == 'review' and 'меньше индивидуального' in result.message for result in results))


class AutomaticPaymentAllocationTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='PAY-1')
        self.old = BillingPeriod.objects.create(starts=date(2026, 6, 1), ends=date(2026, 7, 1))
        self.current = BillingPeriod.objects.create(starts=date(2026, 7, 1), ends=date(2026, 8, 1))
        self.old_charge = Charge.objects.create(
            account=self.account, period=self.old, kind='service', amount=Decimal('100'), status='approved',
        )
        self.current_charge = Charge.objects.create(
            account=self.account, period=self.current, kind='service', amount=Decimal('50'), status='approved',
        )

    def policy(self, method):
        return BillingPolicy.objects.create(
            name=f'Оплата {method}', is_default=True, payment_allocation=method,
        )

    def payment(self, amount='120', reference=''):
        return Payment.objects.create(
            account=self.account, paid_on=date(2026, 7, 15), amount=Decimal(amount),
            method='bank', status='confirmed', reference=reference,
        )

    def test_oldest_debt_is_paid_first_and_repeat_is_idempotent(self):
        self.policy('oldest')
        payment = self.payment()
        allocations, message = allocate_payment(payment)
        self.assertEqual([(item.charge, item.amount) for item in allocations], [
            (self.old_charge, Decimal('100')), (self.current_charge, Decimal('20')),
        ])
        self.assertIn('осталось нераспределено 0', message)
        again, _ = allocate_payment(payment)
        self.assertEqual(again, [])
        self.assertEqual(PaymentAllocation.objects.count(), 2)

    def test_current_period_is_paid_before_old_debt(self):
        self.policy('current')
        allocations, _ = allocate_payment(self.payment())
        self.assertEqual([(item.charge, item.amount) for item in allocations], [
            (self.current_charge, Decimal('50')), (self.old_charge, Decimal('70')),
        ])

    def test_reference_and_manual_modes_do_not_guess(self):
        policy = self.policy('reference')
        payment = self.payment(amount='40', reference='Оплата за 07.2026')
        allocations, _ = allocate_payment(payment)
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].charge, self.current_charge)
        policy.payment_allocation = 'manual'
        policy.save()
        manual = self.payment(amount='10')
        allocations, message = allocate_payment(manual)
        self.assertEqual(allocations, [])
        self.assertIn('только вручную', message)

    def test_pending_payment_and_overallocation_are_rejected(self):
        self.policy('oldest')
        pending = Payment.objects.create(
            account=self.account, paid_on=date(2026, 7, 15), amount=Decimal('10'),
            method='bank', status='pending',
        )
        with self.assertRaises(ValidationError):
            allocate_payment(pending)
        confirmed = self.payment(amount='200')
        allocate_payment(confirmed)
        extra = self.payment(amount='10')
        with self.assertRaises(ValidationError):
            PaymentAllocation.objects.create(payment=extra, charge=self.old_charge, amount=Decimal('1'))

    def test_reversed_payment_no_longer_covers_the_charge(self):
        self.policy('oldest')
        original = self.payment(amount='100')
        allocate_payment(original)
        original.status = 'reversed'
        original.save()
        replacement = self.payment(amount='100')
        allocations, _ = allocate_payment(replacement)
        self.assertEqual(len(allocations), 1)
        self.assertEqual(allocations[0].charge, self.old_charge)
        self.assertEqual(allocations[0].amount, Decimal('100'))


class FinancialStatementTests(MFAAccessMixin, TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        call_command('setup_roles', stdout=StringIO())
        self.manager = User.objects.create_user(username='statement-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        self.operator = User.objects.create_user(username='statement-operator', is_staff=True)
        self.operator.groups.add(Group.objects.get(name='Оператор воды'))
        self.account = Account.objects.create(number='ST-1', plot='Участок сверки')
        self.period = BillingPeriod.objects.create(starts=date(2026, 7, 1), ends=date(2026, 8, 1))
        self.approved = Charge.objects.create(
            account=self.account, period=self.period, kind='service', amount=Decimal('150'), status='approved',
            notes='Целевой взнос',
        )
        self.draft = Charge.objects.create(
            account=self.account, period=self.period, kind='adjustment', amount=Decimal('20'), status='draft',
        )
        self.payment = Payment.objects.create(
            account=self.account, paid_on=date(2026, 7, 15), amount=Decimal('70'), method='bank',
            status='confirmed', reference='=опасная формула',
        )

    def test_balance_uses_only_approved_charges_and_confirmed_payments(self):
        totals = account_totals(self.account)
        self.assertEqual(totals['charges'], Decimal('150.00'))
        self.assertEqual(totals['payments'], Decimal('70.00'))
        self.assertEqual(totals['balance'], Decimal('80.00'))

    def test_manager_can_open_printable_statement_and_operator_cannot(self):
        url = f'/admin/water/account/{self.account.pk}/statement/'
        self.login_as(self.manager)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Финансовая карточка')
        self.assertContains(response, '80,00')
        self.assertContains(response, 'Печать / сохранить в PDF')
        self.login_as(self.operator)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_statement_csv_is_audited_and_neutralizes_formulas(self):
        from django.contrib.admin.models import LogEntry
        self.login_as(self.manager)
        response = self.client.get(f'/admin/water/account/{self.account.pk}/statement/?export=csv')
        self.assertEqual(response.status_code, 200)
        report = response.content.decode('utf-8-sig')
        self.assertIn("'=опасная формула", report)
        self.assertTrue(LogEntry.objects.filter(
            user=self.manager, object_id=str(self.account.pk), change_message='Экспорт финансовой сверки CSV',
        ).exists())

    def test_admin_actions_approve_and_cancel_only_drafts_with_history(self):
        self.login_as(self.manager)
        response = self.client.post('/admin/water/charge/', {
            'action': 'approve_drafts', '_selected_action': [self.draft.pk, self.approved.pk],
        })
        self.assertEqual(response.status_code, 302)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.status, 'approved')
        self.assertEqual(self.draft.history.first().history_user, self.manager)
        other = Charge.objects.create(
            account=self.account, period=self.period, kind='adjustment', amount=Decimal('5'), status='draft',
        )
        response = self.client.post('/admin/water/charge/', {
            'action': 'cancel_drafts', '_selected_action': [other.pk],
        })
        self.assertEqual(response.status_code, 302)
        other.refresh_from_db()
        self.assertEqual(other.status, 'cancelled')

    def test_period_and_financial_state_transitions_are_guarded(self):
        self.period.status = 'approved'
        with self.assertRaises(ValidationError):
            self.period.save()
        self.approved.status = 'draft'
        with self.assertRaises(ValidationError):
            self.approved.save()
        self.payment.status = 'pending'
        with self.assertRaises(ValidationError):
            self.payment.save()


class SafeImportTests(MFAAccessMixin, TestCase):
    def csv_upload(self, body=None, name='registry.csv'):
        content = body or (
            'Лицевой счёт;Участок;Адрес;Кадастровый номер;Площадь;ФИО;Телефон;Email\n'
            'A-1;12;ул. Садовая;90:01:000000:1;600,5;Иванов Иван Иванович;+79990000000;owner@example.test\n'
        )
        return SimpleUploadedFile(name, content.encode('utf-8-sig'), content_type='text/csv')

    def test_csv_is_staged_without_touching_working_registry(self):
        batch = stage_import(self.csv_upload(), date(2026, 1, 1))
        self.assertEqual(batch.row_count, 1)
        row = batch.rows.get()
        self.assertEqual(row.status, 'ready')
        self.assertEqual(row.area_m2, Decimal('600.5'))
        self.assertFalse(Account.objects.exists())
        self.assertFalse(Person.objects.exists())
        self.assertFalse(LandPlot.objects.exists())
        with self.assertRaises(ValidationError):
            stage_import(self.csv_upload(), date(2026, 1, 1))

    def test_review_rows_preserve_problems_without_applying(self):
        upload = self.csv_upload(
            'Лицевой счёт;Участок;Площадь;ФИО\n'
            'A-1;12;bad;=FORMULA()\n'
            'A-1;12;100;Петров Пётр\n'
        )
        batch = stage_import(upload, date(2026, 1, 1))
        rows = list(batch.rows.order_by('row_number'))
        self.assertEqual([row.status for row in rows], ['review', 'review'])
        self.assertIn('формула', rows[0].issues.lower())
        self.assertIn('Повтор', rows[1].issues)

    def test_ready_row_applies_once_and_creates_audited_links(self):
        batch = stage_import(self.csv_upload(), date(2026, 1, 1))
        row = apply_import_row(batch.rows.get())
        self.assertEqual(row.status, 'applied')
        self.assertEqual(Account.objects.get().number, 'A-1')
        self.assertEqual(LandPlot.objects.get().account, Account.objects.get())
        self.assertEqual(Person.objects.get().full_name, 'Иванов Иван Иванович')
        self.assertEqual(PlotRelation.objects.get().starts, date(2026, 1, 1))
        self.assertEqual(PlotRelation.objects.get().document, 'Импорт; требует сверки с документами')
        self.assertEqual(ImportBatch.objects.get().status, 'applied')
        with self.assertRaises(ValidationError):
            apply_import_row(row)

    def test_xlsx_is_supported_and_formulas_are_quarantined(self):
        from io import BytesIO
        from openpyxl import Workbook
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Реестр'
        sheet.append(['Лицевой счёт', 'Участок', 'ФИО'])
        sheet.append(['X-1', '77', '=CONCAT("Иванов"," Иван")'])
        stream = BytesIO()
        workbook.save(stream)
        workbook.close()
        upload = SimpleUploadedFile('registry.xlsx', stream.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        batch = stage_import(upload, date(2026, 1, 1))
        self.assertEqual(batch.sheet, 'Реестр')
        self.assertEqual(batch.rows.get().status, 'review')
        self.assertIn('формула', batch.rows.get().issues.lower())

    def test_manager_upload_page_and_operator_isolation(self):
        from django.contrib.auth.models import Group
        call_command('setup_roles', stdout=StringIO())
        manager = User.objects.create_user(username='import-manager', is_staff=True)
        manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        operator = User.objects.create_user(username='import-operator', is_staff=True)
        operator.groups.add(Group.objects.get(name='Оператор воды'))
        url = '/admin/water/importbatch/upload/'
        self.login_as(operator)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login_as(manager)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {
            'file': self.csv_upload(), 'effective_date': '2026-01-01', 'notes': 'Первичная сверка',
        })
        self.assertEqual(response.status_code, 302)
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.notes, 'Первичная сверка')
        self.assertEqual(batch.history.first().history_user, manager)


class ResidentPortalTests(MFAAccessMixin, TestCase):
    password = 'resident-unique-password-2026!'

    def setUp(self):
        self.account = Account.objects.create(number='CAB-1', plot='Участок кабинета')
        self.other = Account.objects.create(number='CAB-2', plot='Чужой участок')
        self.node = SupplyNode.objects.create(name='Узел кабинета')
        self.meter = Meter.objects.create(
            serial='CAB-METER', kind='individual', node=self.node, account=self.account,
            commissioned_on=date(2026, 1, 1),
        )
        self.other_meter = Meter.objects.create(
            serial='OTHER-METER', kind='individual', node=self.node, account=self.other,
            commissioned_on=date(2026, 1, 1),
        )
        self.period = BillingPeriod.objects.create(starts=date(2026, 7, 1), ends=date(2026, 8, 1))
        Charge.objects.create(
            account=self.account, period=self.period, kind='service', amount=Decimal('100'), status='approved',
        )
        Charge.objects.create(
            account=self.account, period=self.period, kind='adjustment', amount=Decimal('999'), status='draft',
        )
        Payment.objects.create(
            account=self.account, paid_on=date(2026, 7, 15), amount=Decimal('40'), method='bank', status='confirmed',
        )

    def create_resident(self, username='resident@example.test', account=None):
        user = User.objects.create_user(username=username, email=username, password=self.password)
        ResidentAccess.objects.create(
            user=user, account=account or self.account, role='owner', starts=date(2026, 1, 1),
        )
        return user

    def test_one_time_invite_creates_nonstaff_user_and_cannot_be_reused(self):
        invite, raw = issue_invite(self.account, 'new-resident@example.test', 'owner')
        self.assertNotIn(raw, invite.token_hash)
        url = f'/admin/cabinet/invite/{raw}/'
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {'password1': self.password, 'password2': self.password})
        self.assertRedirects(response, '/admin/cabinet/')
        user = User.objects.get(email='new-resident@example.test')
        self.assertFalse(user.is_staff)
        self.assertTrue(ResidentAccess.objects.filter(user=user, account=self.account).exists())
        invite.refresh_from_db()
        self.assertIsNotNone(invite.used_at)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 410)

    def test_resident_login_and_staff_otp_boundary(self):
        resident = self.create_resident()
        response = self.client.post('/admin/cabinet/login/', {
            'username': resident.username, 'password': self.password,
        })
        self.assertRedirects(response, '/admin/cabinet/')
        self.client.logout()
        staff = User.objects.create_user(username='portal-staff', password=self.password, is_staff=True)
        response = self.client.post('/admin/cabinet/login/', {
            'username': staff.username, 'password': self.password,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Сотрудники входят через защищённую административную форму')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_dashboard_exposes_only_linked_account_and_approved_finance(self):
        resident = self.create_resident()
        self.client.force_login(resident)
        response = self.client.get('/admin/cabinet/')
        self.assertContains(response, 'CAB-1')
        self.assertNotContains(response, 'CAB-2')
        self.assertContains(response, '60,00')
        detail = self.client.get(f'/admin/cabinet/account/{self.account.pk}/')
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, '100,00')
        self.assertNotContains(detail, '999,00')
        self.assertEqual(self.client.get(f'/admin/cabinet/account/{self.other.pk}/').status_code, 404)

    def test_resident_can_submit_only_own_meter_reading_with_attribution(self):
        resident = self.create_resident()
        self.client.force_login(resident)
        response = self.client.post(
            f'/admin/cabinet/account/{self.account.pk}/meter/{self.meter.pk}/reading/',
            {'date': '2026-09-18', 'value': '123.456', 'notes': 'Передано владельцем'},
        )
        self.assertRedirects(response, f'/admin/cabinet/account/{self.account.pk}/')
        reading = Reading.objects.get()
        self.assertEqual(reading.history.first().history_user, resident)
        self.assertEqual(reading.history.first().history_change_reason, 'Показание передано через личный кабинет')
        self.assertEqual(self.client.post(
            f'/admin/cabinet/account/{self.account.pk}/meter/{self.other_meter.pk}/reading/',
            {'date': '2026-09-18', 'value': '1'},
        ).status_code, 404)

    def test_revoked_expired_and_existing_email_invites_are_safe(self):
        existing = self.create_resident()
        invite, raw = issue_invite(self.other, existing.email, 'owner')
        url = f'/admin/cabinet/invite/{raw}/'
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(existing)
        self.assertContains(self.client.get(url), 'Подключить лицевой счёт')
        self.assertRedirects(self.client.post(url), f'/admin/cabinet/account/{self.other.pk}/')
        self.assertTrue(ResidentAccess.objects.filter(user=existing, account=self.other).exists())
        self.assertEqual(self.client.get(url).status_code, 410)
        revoked, revoked_raw = issue_invite(self.other, 'revoked@example.test', 'owner')
        revoked.revoked = True
        revoked.save()
        self.assertEqual(self.client.get(f'/admin/cabinet/invite/{revoked_raw}/').status_code, 410)
        expired, expired_raw = issue_invite(self.other, 'expired@example.test', 'owner')
        expired.expires_at = timezone.now() - timedelta(minutes=1)
        expired.save()
        self.assertEqual(self.client.get(f'/admin/cabinet/invite/{expired_raw}/').status_code, 410)

    def test_existing_email_invite_rejects_wrong_or_ambiguous_user(self):
        existing = self.create_resident()
        wrong = self.create_resident('wrong@example.test', self.other)
        invite, raw = issue_invite(self.other, existing.email, 'payer')
        url = f'/admin/cabinet/invite/{raw}/'
        self.client.force_login(wrong)
        self.assertEqual(self.client.get(url).status_code, 403)
        User.objects.create_user(username='duplicate-email', email=existing.email, password=self.password)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 409)

    def test_new_invite_revokes_previous_unused_link(self):
        first, first_raw = issue_invite(self.account, 'replace@example.test', 'owner')
        second, second_raw = issue_invite(self.account, 'REPLACE@example.test', 'payer')
        first.refresh_from_db()
        self.assertTrue(first.revoked)
        self.assertFalse(second.revoked)
        self.assertEqual(self.client.get(f'/admin/cabinet/invite/{first_raw}/').status_code, 410)
        self.assertEqual(self.client.get(f'/admin/cabinet/invite/{second_raw}/').status_code, 200)

    def test_manager_can_issue_invite_but_operator_cannot(self):
        from django.contrib.auth.models import Group
        call_command('setup_roles', stdout=StringIO())
        manager = User.objects.create_user(username='cabinet-manager', is_staff=True)
        manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        operator = User.objects.create_user(username='cabinet-operator', is_staff=True)
        operator.groups.add(Group.objects.get(name='Оператор воды'))
        url = f'/admin/water/account/{self.account.pk}/invite/'
        self.login_as(operator)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login_as(manager)
        response = self.client.post(url, {'email': 'invitee@example.test', 'role': 'owner'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/admin/cabinet/invite/')
        self.assertEqual(ResidentInvite.objects.count(), 1)
        self.assertEqual(ResidentInvite.objects.get().history.first().history_user, manager)

    def test_password_change_and_one_time_recovery(self):
        resident = self.create_resident()
        self.client.force_login(resident)
        changed_password = 'resident-changed-password-2026!'
        response = self.client.post('/admin/cabinet/password/', {
            'old_password': self.password, 'new_password1': changed_password, 'new_password2': changed_password,
        })
        self.assertRedirects(response, '/admin/cabinet/')
        self.assertEqual(self.client.get('/admin/cabinet/').status_code, 200)
        resident.refresh_from_db()
        self.assertTrue(resident.check_password(changed_password))

        reset, raw = issue_password_reset(resident)
        recovered_password = 'resident-recovered-password-2026!'
        url = f'/admin/cabinet/reset/{raw}/'
        self.client.logout()
        response = self.client.post(url, {
            'password1': recovered_password, 'password2': recovered_password,
        })
        self.assertRedirects(response, '/admin/cabinet/')
        resident.refresh_from_db()
        reset.refresh_from_db()
        self.assertTrue(resident.check_password(recovered_password))
        self.assertIsNotNone(reset.used_at)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 410)

    def test_manager_can_create_recovery_link_but_operator_cannot(self):
        from django.contrib.auth.models import Group
        call_command('setup_roles', stdout=StringIO())
        resident = self.create_resident()
        access = ResidentAccess.objects.get(user=resident, account=self.account)
        manager = User.objects.create_user(username='recovery-manager', is_staff=True)
        manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        operator = User.objects.create_user(username='recovery-operator', is_staff=True)
        operator.groups.add(Group.objects.get(name='Оператор воды'))
        url = f'/admin/water/residentaccess/{access.pk}/reset-password/'
        self.login_as(operator)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login_as(manager)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/admin/cabinet/reset/')
        self.assertEqual(ResidentPasswordReset.objects.count(), 1)
        self.assertEqual(ResidentPasswordReset.objects.get().history.first().history_user, manager)

    def test_resident_creates_and_reads_only_own_appeals(self):
        category = AppealCategory.objects.create(name='Перерасчёт')
        hidden_category = AppealCategory.objects.create(name='Служебная', active=False)
        resident = self.create_resident()
        other_resident = self.create_resident('other-resident@example.test', self.other)
        self.client.force_login(resident)
        form_page = self.client.get(f'/admin/cabinet/account/{self.account.pk}/appeal/new/')
        self.assertContains(form_page, 'Перерасчёт')
        self.assertNotContains(form_page, 'Служебная')
        response = self.client.post(f'/admin/cabinet/account/{self.account.pk}/appeal/new/', {
            'category': category.pk, 'subject': 'Проверить сумму', 'message': 'Прошу выполнить сверку.',
        })
        appeal = ResidentAppeal.objects.get()
        self.assertRedirects(response, f'/admin/cabinet/account/{self.account.pk}/appeal/{appeal.pk}/')
        self.assertEqual(appeal.author, resident)
        self.assertEqual(appeal.history.first().history_user, resident)
        appeal.status = 'awaiting_resident'
        appeal._change_reason = 'Нужно уточнение'
        appeal.save()
        response = self.client.post(
            f'/admin/cabinet/account/{self.account.pk}/appeal/{appeal.pk}/',
            {'body': 'Дополняю сведения по запросу.'},
        )
        self.assertRedirects(response, f'/admin/cabinet/account/{self.account.pk}/appeal/{appeal.pk}/')
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, 'in_progress')
        clarification = ResidentAppealMessage.objects.get(appeal=appeal)
        self.assertEqual(clarification.history.first().history_user, resident)
        foreign = ResidentAppeal.objects.create(
            account=self.other, author=other_resident, category=category,
            subject='Чужое обращение', message='Закрытая информация',
        )
        self.assertEqual(self.client.get(
            f'/admin/cabinet/account/{self.other.pk}/appeal/{foreign.pk}/',
        ).status_code, 404)

    def test_manager_answer_is_published_with_attribution(self):
        from django.contrib.auth.models import Group
        category = AppealCategory.objects.create(name='Документы')
        resident = self.create_resident()
        appeal = ResidentAppeal.objects.create(
            account=self.account, author=resident, category=category,
            subject='Нужна справка', message='Прошу выдать справку.',
        )
        call_command('setup_roles', stdout=StringIO())
        manager = User.objects.create_user(username='appeal-manager', is_staff=True)
        manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        self.login_as(manager)
        response = self.client.post(f'/admin/water/residentappeal/{appeal.pk}/change/', {
            'version': appeal.version, 'status': 'resolved', 'response': 'Справка готова.',
            'change_reason': 'Подготовлен ответ',
        })
        self.assertEqual(response.status_code, 302)
        appeal.refresh_from_db()
        self.assertEqual(appeal.responded_by, manager)
        self.assertIsNotNone(appeal.responded_at)

    def test_private_document_requires_matching_active_access(self):
        resident = self.create_resident()
        other_resident = self.create_resident('document-other@example.test', self.other)
        category = DocumentCategory.objects.get(name='Квитанция')
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            document = AccountDocument.objects.create(
                account=self.account, category=category, title='Квитанция за август',
                document=SimpleUploadedFile('август.pdf', b'%PDF-test', content_type='application/pdf'),
            )
            url = f'/admin/cabinet/account/{self.account.pk}/document/{document.pk}/'
            self.client.force_login(resident)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), b'%PDF-test')
            self.client.force_login(other_resident)
            self.assertEqual(self.client.get(url).status_code, 404)
            document.visible_to_residents = False
            document.save()
            self.client.force_login(resident)
            self.assertEqual(self.client.get(url).status_code, 404)
class ControllerSubmissionTests(MFAAccessMixin, TestCase):
    def setUp(self):
        from django.contrib.auth.models import Group
        call_command('setup_roles', stdout=StringIO())
        self.controller = User.objects.create_user(username='controller-test', is_staff=True)
        self.controller.groups.add(Group.objects.get(name='Контролёр воды'))
        self.manager = User.objects.create_user(username='controller-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        self.account = Account.objects.create(number='77', plot='Лесная 7')
        self.node = SupplyNode.objects.create(name='Узел контролёра')
        self.meter = Meter.objects.create(serial='CTRL-1', kind='individual', node=self.node, account=self.account)

    def test_controller_submits_photo_and_manager_approves(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.login_as(self.controller)
            response = self.client.post('/admin/water/controllerreadingsubmission/capture/', {
                'meter': self.meter.pk,
                'date': timezone.localdate().isoformat(),
                'value': '123.45',
                'photo': SimpleUploadedFile('meter.jpg', b'\xff\xd8\xff\xe0test', content_type='image/jpeg'),
                'notes': 'Обход',
            })
            self.assertEqual(response.status_code, 302)
            submission = ControllerReadingSubmission.objects.get()
            self.assertEqual(submission.status, 'pending')
            self.assertEqual(Reading.objects.count(), 0)
            self.assertEqual(self.client.post(f'/admin/water/controllerreadingsubmission/{submission.pk}/approve/').status_code, 403)

            self.login_as(self.manager)
            response = self.client.post(f'/admin/water/controllerreadingsubmission/{submission.pk}/approve/')
            self.assertEqual(response.status_code, 302)
            submission.refresh_from_db()
            self.assertEqual(submission.status, 'approved')
            self.assertEqual(submission.reading.value, Decimal('123.450'))
            self.assertEqual(submission.reading.history.first().history_user, self.manager)
