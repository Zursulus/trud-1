"""Only synthetic accounts/readings; no production fixtures or credentials."""
import csv
import importlib
import io
from types import SimpleNamespace
from datetime import date
from decimal import Decimal
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.apps import apps
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .billing import account_totals, calculate_period
from .models import (Account, BillingPeriod, BillingPolicy, Charge, GroupConsumption,
                     Membership, Meter, Reading, SupplyNode, Tariff, User, WaterGroup)
from .tariff_models import TariffRecalculation
from .tariff_recalculation import create_recalculation, decide_recalculation, money
from .tariff_views import RecalculationForm


class TariffRecalculationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = User.objects.create_user(username='synthetic-chair', is_staff=True, is_superuser=True)
        cls.period = BillingPeriod.objects.create(starts=date(2026, 1, 1), ends=date(2026, 2, 1))
        cls.node = SupplyNode.objects.create(name='Синтетический узел')
        cls.group = WaterGroup.objects.create(name='Синтетическая линия', node=cls.node, source='reported')
        cls.account = Account.objects.create(number='SYN-001', plot='Синтетический участок 1')
        Membership.objects.create(account=cls.account, group=cls.group, starts=cls.period.starts)
        main = Meter.objects.create(serial='SYN-OSN', kind='main', node=cls.node)
        cls.individual = Meter.objects.create(serial='SYN-IND', kind='individual', node=cls.node, account=cls.account)
        for meter, volume in ((main, '489'), (cls.individual, '386')):
            Reading.objects.create(meter=meter, date=cls.period.starts, value=Decimal('0'))
            Reading.objects.create(meter=meter, date=cls.period.ends, value=Decimal(volume))
        GroupConsumption.objects.create(group=cls.group, starts=cls.period.starts, ends=cls.period.ends,
                                        volume=Decimal('450'), reported_by='Синтетический старший')
        cls.original = Charge.objects.create(account=cls.account, period=cls.period, kind='water',
                                             volume=Decimal('386'), rate=Decimal('109.34'),
                                             amount=Decimal('42206.24'), status='approved')
        cls.period.status = 'approved'
        cls.period.save()

    def setUp(self):
        self.client.force_login(self.actor)

    def create(self, **overrides):
        data = dict(actor=self.actor, period=self.period, rate=Decimal('109.34'), starts=self.period.starts,
                    basis='Синтетический счёт №1')
        data.update(overrides)
        return create_recalculation(**data)

    def detail_url(self, item):
        return reverse('water_tariff_recalculation_detail', args=[item.pk])

    def form_data(self, **extra):
        data = dict(period=self.period.pk, rate='109.34', starts='2026-01-01', ends='',
                    basis='Синтетический счёт №1', loss_distribution='node_excel')
        data.update(extra)
        return data

    def user_with(self, *codes):
        user = User.objects.create_user(username=f'synthetic-staff-{User.objects.count()}', is_staff=True)
        user.user_permissions.set(Permission.objects.filter(content_type__app_label='water', codename__in=codes))
        return user

    def test_preview_formula_rounding_and_no_ledger_writes(self):
        history_before = Charge.history.count()
        item = self.create()
        row = item.result['accounts'][0]
        self.assertEqual(item.result['nodes'][0]['rate'], '138.52')
        self.assertEqual(row['new'], '53468.72')
        self.assertEqual(row['delta'], '11262.48')
        self.assertEqual(row['percent'], '26.68')
        self.assertEqual(item.result['totals']['input'], '489.000')
        self.assertEqual(item.result['totals']['loss'], '103.000')
        self.assertEqual(Charge.objects.count(), 1)
        self.assertEqual(Charge.history.count(), history_before)
        self.original.refresh_from_db()
        self.assertEqual(self.original.amount, Decimal('42206.24'))
        self.assertEqual(money(Decimal('1.005')), Decimal('1.01'))

    def test_approval_preserves_original_adds_adjustment_and_audit(self):
        item = self.create()
        decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)
        correction = Charge.objects.get(tariff_recalculation=item)
        self.assertEqual(correction.amount, Decimal('11262.48'))
        self.assertEqual(correction.kind, 'adjustment')
        self.assertEqual(correction.status, 'approved')
        self.assertEqual(correction.history.get().history_user, self.actor)
        self.assertIn(str(item.pk), correction.history.get().history_change_reason)
        self.assertIn(item.basis, correction.notes)
        self.assertEqual(account_totals(self.account)['charges'], Decimal('53468.72'))
        self.original.refresh_from_db()
        self.assertEqual(self.original.amount, Decimal('42206.24'))
        self.assertEqual(self.original.history.count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.decided_by, self.actor)
        self.assertEqual(item.history.count(), 2)
        self.assertTrue(LogEntry.objects.filter(user=self.actor, change_message__contains=item.basis).exists())
        with self.assertRaises(ValidationError):
            decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)
        self.assertEqual(Charge.objects.count(), 2)

    def test_second_recalculation_accounts_for_previous_corrections(self):
        first = self.create()
        decide_recalculation(recalc_id=first.pk, actor=self.actor, approve=True)
        second = self.create(rate=Decimal('91.00'))
        self.assertEqual(second.result['accounts'][0]['old'], '53468.72')
        decide_recalculation(recalc_id=second.pk, actor=self.actor, approve=True)
        self.assertEqual(account_totals(self.account)['charges'], Decimal(second.result['accounts'][0]['new']))
        self.assertEqual(Charge.objects.count(), 3)

    def test_cancel_draft_never_changes_charges(self):
        item = self.create()
        before = Charge.history.count()
        decide_recalculation(recalc_id=item.pk, actor=self.actor)
        item.refresh_from_db()
        self.assertEqual(item.status, 'cancelled')
        self.assertEqual(Charge.history.count(), before)
        self.assertEqual(Charge.objects.count(), 1)
        with self.assertRaises(ValidationError):
            decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)

    def test_missing_meter_and_boundary_are_review_without_amounts(self):
        missing = Account.objects.create(number='SYN-NO-METER')
        Membership.objects.create(account=missing, group=self.group, starts=self.period.starts)
        boundary = Account.objects.create(number='SYN-NO-READING')
        Membership.objects.create(account=boundary, group=self.group, starts=self.period.starts)
        Meter.objects.create(serial='SYN-MISSING', kind='individual', account=boundary, node=self.node)
        item = self.create()
        rows = {row['account_id']: row for row in item.result['accounts']}
        for account in (missing, boundary):
            self.assertIsNone(rows[account.pk]['new'])
            self.assertIsNone(rows[account.pk]['delta'])
            self.assertTrue(rows[account.pk]['reason'])
        self.assertIn('нет активного', rows[missing.pk]['reason'])
        self.assertIn('нет граничных', rows[boundary.pk]['reason'])
        decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)
        self.assertFalse(Charge.objects.filter(account__in=[missing, boundary]).exists())

    def test_missing_main_reading_blocks_node(self):
        other = SupplyNode.objects.create(name='Синтетический узел без ОСН')
        group = WaterGroup.objects.create(name='Синтетическая линия без ОСН', node=other)
        account = Account.objects.create(number='SYN-002')
        Membership.objects.create(account=account, group=group, starts=self.period.starts)
        meter = Meter.objects.create(serial='SYN-IND-2', kind='individual', account=account, node=other)
        Reading.objects.create(meter=meter, date=self.period.starts, value=Decimal('0'))
        Reading.objects.create(meter=meter, date=self.period.ends, value=Decimal('1'))
        item = self.create()
        self.assertIsNone(item.result['accounts'][1]['new'])
        self.assertIn('ОСН', item.result['accounts'][1]['reason'])
        self.assertIsNone(item.result['totals']['input'])

    def test_zero_denominator_requires_review(self):
        reading = Reading.objects.get(meter=self.individual, date=self.period.ends)
        reading.value = Decimal('0')
        reading.save()
        item = self.create()
        self.assertIsNone(item.result['accounts'][0]['new'])
        with self.assertRaises(ValidationError):
            decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)

    def test_legacy_line_volume_distribution_is_retained(self):
        item = self.create(loss_distribution='volume')
        # 386 m³ individual + 64 m³ line losses at 109.34; not OSN's 489.
        self.assertEqual(item.result['accounts'][0]['new'], '49203.00')
        self.assertEqual(item.result['groups'][0]['loss'], '64.000')
        decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)
        self.assertEqual(account_totals(self.account)['charges'], Decimal('49203.00'))

    def test_none_distribution_keeps_only_consumption(self):
        item = self.create(loss_distribution='none')
        self.assertEqual(item.result['accounts'][0]['new'], '42205.24')

    def test_stale_readings_reject_approval_atomically(self):
        item = self.create()
        reading = Reading.objects.get(meter=self.individual, date=self.period.ends)
        reading.value = Decimal('385')
        reading.save()
        with self.assertRaisesMessage(ValidationError, 'Данные изменились'):
            decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)
        item.refresh_from_db()
        self.assertEqual(item.status, 'draft')
        self.assertEqual(Charge.objects.count(), 1)

    def test_stale_ledger_rejects_approval(self):
        item = self.create()
        self.original.status = 'cancelled'
        self.original.save()
        with self.assertRaisesMessage(ValidationError, 'Данные изменились'):
            decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)

    def test_closed_period_rejected_for_creation_and_approval(self):
        item = self.create()
        self.period.status = 'closed'
        self.period.save()
        with self.assertRaises(ValidationError):
            self.create()
        with self.assertRaises(ValidationError):
            decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)

    def test_snapshot_cannot_be_edited(self):
        item = self.create()
        item.rate = Decimal('91')
        with self.assertRaises(ValidationError):
            item.save()

    def test_approval_rolls_back_all_writes_on_failure(self):
        item = self.create()
        with patch.object(Charge, 'save', side_effect=ValidationError('Synthetic failure')):
            with self.assertRaises(ValidationError):
                decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)
        item.refresh_from_db()
        self.assertEqual(item.status, 'draft')
        self.assertEqual(item.history.count(), 1)
        self.assertEqual(Charge.objects.count(), 1)

    def test_model_rejects_rewrite_and_unrelated_approved_period_insertion(self):
        self.original.amount = Decimal('1')
        with self.assertRaises(ValidationError):
            self.original.save()
        with self.assertRaises(ValidationError):
            Charge.objects.create(account=self.account, period=self.period, kind='adjustment', amount=Decimal('-1'))
        item = self.create()
        with self.assertRaises(ValidationError):
            Charge.objects.create(account=self.account, period=self.period, kind='adjustment', amount=Decimal('1'),
                                  tariff_recalculation=item, status='approved')

    def test_unauthorized_staff_gets_403_on_all_endpoints(self):
        item = self.create()
        self.client.force_login(self.user_with('view_charge', 'change_charge'))
        for url in (reverse('water_tariff_recalculation'), self.detail_url(item),
                    reverse('water_tariff_recalculation_document', args=[item.pk])):
            self.assertEqual(self.client.get(url).status_code, 403)
        for url in (reverse('water_tariff_recalculation'), self.detail_url(item)):
            self.assertEqual(self.client.post(url, self.form_data()).status_code, 403)
        with self.assertRaises(PermissionDenied):
            self.create(actor=self.user_with('view_charge'))

    def test_draft_permission_does_not_allow_approval(self):
        user = self.user_with('view_tariffrecalculation', 'add_tariffrecalculation')
        item = self.create(actor=user)
        self.client.force_login(user)
        response = self.client.post(self.detail_url(item), {'action': 'approve', 'confirm': 'on', 'version': item.version})
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(self.client.get(self.detail_url(item)), 'name="action" value="approve"')
        with self.assertRaises(PermissionDenied):
            decide_recalculation(recalc_id=item.pk, actor=user, approve=True)

    def test_view_permission_does_not_allow_draft_or_cancel(self):
        item = self.create()
        self.client.force_login(self.user_with('view_tariffrecalculation'))
        self.assertEqual(self.client.get(reverse('water_tariff_recalculation')).status_code, 200)
        self.assertEqual(self.client.post(reverse('water_tariff_recalculation'), self.form_data()).status_code, 403)
        self.assertEqual(self.client.post(self.detail_url(item), {'action': 'cancel', 'version': item.version}).status_code, 403)

    def test_csrf_required_for_create_approve_and_cancel(self):
        item = self.create()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        self.assertEqual(client.post(reverse('water_tariff_recalculation'), self.form_data()).status_code, 403)
        for action in ('approve', 'cancel'):
            self.assertEqual(client.post(self.detail_url(item), {'action': action, 'confirm': 'on', 'version': item.version}).status_code, 403)
        client.get(self.detail_url(item))
        token = client.cookies['csrftoken'].value
        self.assertEqual(client.post(self.detail_url(item), {'action': 'approve', 'confirm': 'on',
                         'version': item.version, 'csrfmiddlewaretoken': token}).status_code, 302)

    def test_confirmation_and_version_required_then_approve(self):
        item = self.create()
        url = self.detail_url(item)
        response = self.client.post(url, {'action': 'approve', 'version': item.version})
        self.assertContains(response, 'Обязательное поле')
        self.assertEqual(Charge.objects.count(), 1)
        self.assertContains(self.client.post(url, {'action': 'approve', 'confirm': 'on', 'version': 999}), 'Версия пересчёта изменилась')
        self.assertRedirects(self.client.post(url, {'action': 'approve', 'confirm': 'on', 'version': item.version}), url)
        self.assertContains(self.client.get(url), 'Утверждён')

    def test_cancel_post_and_get_is_read_only(self):
        item = self.create()
        self.client.get(self.detail_url(item), {'action': 'approve', 'confirm': 'on'})
        item.refresh_from_db()
        self.assertEqual(item.status, 'draft')
        self.assertRedirects(self.client.post(self.detail_url(item), {'action': 'cancel', 'version': item.version}), self.detail_url(item))
        self.assertEqual(Charge.objects.count(), 1)

    def test_form_rejects_precision_dates_blank_basis_and_manual(self):
        for changes in ({'rate': '1.005'}, {'rate': '0'}, {'rate': '-1'}, {'starts': '2026-01-02'},
                        {'ends': '2026-01-20'}, {'ends': '2025-12-31'}, {'basis': ' '}, {'loss_distribution': 'manual'}):
            with self.subTest(changes=changes):
                self.assertFalse(RecalculationForm(data=self.form_data(**changes)).is_valid())
        self.assertTrue(RecalculationForm(data=self.form_data(rate='109,34')).is_valid())

    def test_form_post_creates_preview_and_superuser_sees_prominent_link(self):
        self.assertFalse(self.actor.groups.exists())
        self.assertContains(self.client.get(reverse('admin:index')), reverse('water_tariff_recalculation'))
        self.assertContains(self.client.get(reverse('staff_workspace:finance')), reverse('water_tariff_recalculation'))
        self.assertContains(self.client.get(reverse('water_tariff_recalculation')), 'Рассчитать черновик')
        response = self.client.post(reverse('water_tariff_recalculation'), self.form_data())
        item = TariffRecalculation.objects.get()
        self.assertRedirects(response, self.detail_url(item))
        self.assertContains(self.client.get(self.detail_url(item)), '138,52')
        self.assertEqual(Charge.objects.count(), 1)

    def test_search_node_filter_and_csv_escape(self):
        self.account.plot = '=SYNTHETIC()'
        self.account.save()
        item = self.create()
        response = self.client.get(self.detail_url(item), {'q': 'not-found'})
        self.assertEqual(response.context['rows'], [])
        response = self.client.get(self.detail_url(item), {'node': str(self.node.pk)})
        self.assertEqual(len(response.context['rows']), 1)
        response = self.client.get(self.detail_url(item), {'export': 'csv'})
        self.assertIn('no-store', response['Cache-Control'])
        records = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig')), delimiter=';'))
        self.assertIn("'=SYNTHETIC()", records[-1])
        self.assertIn('53468,72', records[-1])
        self.assertTrue(LogEntry.objects.filter(change_message__contains='Экспорт сравнения').exists())

    def test_optional_document_private_download_and_unsafe_file_rejected(self):
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            # Reuse the project's existing upload safety pipeline; no scanner bypass.
            upload = SimpleUploadedFile('synthetic-basis.exe', b'Synthetic')
            form = RecalculationForm(data=self.form_data(), files={'document': upload})
            self.assertFalse(form.is_valid())
            item = self.create()
            self.assertEqual(self.client.get(reverse('water_tariff_recalculation_document', args=[item.pk])).status_code, 404)

    def test_setup_roles_only_grants_recalculation_to_tsn_administrator(self):
        call_command('setup_roles', stdout=io.StringIO())
        codes = {'view_tariffrecalculation', 'add_tariffrecalculation', 'approve_tariffrecalculation'}
        admin_group = Group.objects.get(name='Администратор ТСН')
        self.assertTrue(codes.issubset(set(admin_group.permissions.values_list('codename', flat=True))))
        for name in ('Оператор воды', 'Контролёр воды', 'Закрытый реестр членов ТСН'):
            self.assertFalse(Group.objects.get(name=name).permissions.filter(codename__in=codes).exists())
        user = User.objects.create_user(username='synthetic-tsn-admin', is_staff=True)
        user.groups.add(admin_group)
        item = self.create(actor=user)
        decide_recalculation(recalc_id=item.pk, actor=user, approve=True)

    def test_role_data_migration_grants_and_reverses(self):
        group = Group.objects.create(name='Администратор ТСН')
        module = importlib.import_module('water.migrations.0036_tariff_recalculation_roles')
        editor = SimpleNamespace(connection=connection)
        module.grant(apps, editor)
        self.assertEqual(group.permissions.filter(codename__contains='tariffrecalculation').count(), 4)
        module.revoke(apps, editor)
        self.assertFalse(group.permissions.filter(codename__contains='tariffrecalculation').exists())

    def test_node_excel_policy_cannot_silently_use_old_calculator(self):
        BillingPolicy.objects.create(name='Синтетическая Excel-политика', is_default=True, loss_distribution='node_excel')
        self.period.status = 'open'
        self.period.save()
        with self.assertRaises(ValidationError):
            calculate_period(self.period, actor=self.actor)
        self.assertEqual(Charge.objects.count(), 1)

    def test_multiple_nodes_use_their_own_denominators(self):
        node = SupplyNode.objects.create(name='Второй синтетический узел')
        group = WaterGroup.objects.create(name='Вторая синтетическая линия', node=node)
        account = Account.objects.create(number='SYN-SECOND-NODE')
        Membership.objects.create(account=account, group=group, starts=self.period.starts)
        for kind, volume in (('main', '200'), ('individual', '100')):
            meter = Meter.objects.create(serial=f'SYN-SECOND-{kind}', kind=kind, node=node,
                                         account=account if kind == 'individual' else None)
            Reading.objects.create(meter=meter, date=self.period.starts, value=Decimal('0'))
            Reading.objects.create(meter=meter, date=self.period.ends, value=Decimal(volume))
        item = self.create()
        self.assertEqual([row['rate'] for row in item.result['accounts']], ['138.52', '218.68'])
        self.assertEqual(item.result['accounts'][1]['percent'], None)
        self.assertEqual(item.result['nodes'][0]['formula'], '109,34 × 489 / 386 = 138,52')

    def test_financial_review_does_not_remove_confirmed_volume_from_denominator(self):
        self.period.status = 'open'
        self.period.save()
        Charge.objects.create(account=self.account, period=self.period, kind='adjustment',
                              status='approved', amount=Decimal('-1'))
        item = self.create()
        self.assertIn('ручные корректировки', item.result['accounts'][0]['reason'])
        self.assertIsNone(item.result['accounts'][0]['new'])
        self.assertEqual(item.result['nodes'][0]['rate'], '138.52')
        self.assertEqual(item.result['nodes'][0]['confirmed'], '386.000')

    def test_rate_uses_half_up_before_amount(self):
        for kind, value in (('main', '9'), ('individual', '8')):
            reading = Reading.objects.get(meter__kind=kind, date=self.period.ends)
            reading.value = Decimal(value)
            reading.save()
        item = self.create(rate=Decimal('1.00'))
        self.assertEqual(item.result['nodes'][0]['rate'], '1.13')
        self.assertEqual(item.result['accounts'][0]['new'], '9.04')

    def test_pending_water_charges_are_previewed_then_cancelled_with_history(self):
        self.period.status = 'open'
        self.period.save()
        pending = Charge.objects.create(account=self.account, period=self.period, kind='loss', amount=Decimal('1'))
        item = self.create()
        row = item.result['accounts'][0]
        self.assertEqual(row['reason'], '')
        self.assertEqual(row['old'], '42207.24')
        self.assertEqual(row['delta'], '11261.48')
        self.assertEqual(row['posting_delta'], '11262.48')
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'draft')
        decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'cancelled')
        self.assertEqual(pending.history.count(), 2)
        self.assertEqual(pending.history.first().history_user, self.actor)
        self.assertEqual(account_totals(self.account)['charges'], Decimal('53468.72'))

    def test_meter_replacement_requires_manual_review(self):
        self.individual.retired_on = date(2026, 1, 15)
        first = Reading.objects.get(meter=self.individual, date=self.period.ends)
        first.date = date(2026, 1, 15)
        first.save()
        self.individual.save()
        item = self.create()
        self.assertIn('Замена', item.result['accounts'][0]['reason'])
        self.assertIsNone(item.result['accounts'][0]['new'])

    def test_no_membership_is_not_guessed(self):
        account = Account.objects.create(number='SYN-UNASSIGNED')
        item = self.create()
        row = next(row for row in item.result['accounts'] if row['account_id'] == account.pk)
        self.assertIn('однозначной линии', row['reason'])
        self.assertIsNone(row['new'])

    def test_required_mfa_still_guards_the_new_screen(self):
        with override_settings(TRUD_MFA_REQUIRED_USERNAMES=[self.actor.username]):
            self.assertEqual(self.client.get(reverse('water_tariff_recalculation')).status_code, 302)

    def test_valid_optional_file_is_downloaded_only_through_permission_view(self):
        with TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            upload = SimpleUploadedFile('synthetic-basis.pdf', b'%PDF-1.4\n% Synthetic invoice only\n%%EOF')
            item = self.create(document=upload)
            url = reverse('water_tariff_recalculation_document', args=[item.pk])
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4\n% Synthetic invoice only\n%%EOF')
            self.assertIn('attachment', response['Content-Disposition'])
            self.assertIn('no-store', response['Cache-Control'])
            self.client.force_login(self.user_with('view_charge'))
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_partial_main_meter_is_not_silently_ignored(self):
        Meter.objects.create(serial='SYN-PARTIAL-MAIN', kind='main', node=self.node,
                             commissioned_on=date(2026, 1, 15))
        item = self.create()
        self.assertIsNone(item.result['nodes'][0]['input'])
        self.assertIsNone(item.result['accounts'][0]['new'])
        self.assertIn('ОСН внутри периода', item.result['accounts'][0]['reason'])

    def test_approval_validation_errors_render_on_decision_form(self):
        item = self.create()
        with patch('water.tariff_views.decide_recalculation',
                   side_effect=ValidationError({'amount': 'Синтетическая ошибка суммы'})):
            response = self.client.post(self.detail_url(item), {
                'action': 'approve', 'confirm': 'on', 'version': item.version,
            })
        self.assertContains(response, 'Синтетическая ошибка суммы')
        self.assertEqual(Charge.objects.count(), 1)

    def test_period_with_only_draft_charges_can_be_recalculated(self):
        self.period.status = 'open'
        self.period.save()
        self.original.status = 'cancelled'
        self.original.save()
        pending = Charge.objects.create(account=self.account, period=self.period, kind='water',
                                        volume=Decimal('386'), rate=Decimal('109.34'), amount=Decimal('42205.24'))
        item = self.create()
        self.assertEqual(item.result['accounts'][0]['old'], '42205.24')
        self.assertEqual(item.result['accounts'][0]['approved_old'], '0')
        self.assertEqual(Charge.objects.filter(status='approved').count(), 0)
        decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'cancelled')
        self.assertEqual(account_totals(self.account)['charges'], Decimal('53468.72'))

    def test_cancel_recalculation_preserves_existing_draft_charges(self):
        self.period.status = 'open'
        self.period.save()
        pending = Charge.objects.create(account=self.account, period=self.period, kind='loss', amount=Decimal('1'))
        item = self.create()
        decide_recalculation(recalc_id=item.pk, actor=self.actor)
        pending.refresh_from_db()
        self.assertEqual(pending.status, 'draft')
        self.assertEqual(pending.history.count(), 1)

    def test_approved_recalculation_blocks_legacy_rebilling(self):
        self.period.status = 'open'
        self.period.save()
        BillingPolicy.objects.create(name='Синтетическая прежняя политика', is_default=True)
        Tariff.objects.create(name='Синтетический прежний тариф', rate=Decimal('109.34'), starts=self.period.starts)
        item = self.create()
        decide_recalculation(recalc_id=item.pk, actor=self.actor, approve=True)
        before = Charge.objects.count(), Charge.history.count()
        with self.assertRaisesMessage(ValidationError, 'новую версию пересчёта'):
            calculate_period(self.period, actor=self.actor)
        self.assertEqual((Charge.objects.count(), Charge.history.count()), before)
