from datetime import date
from decimal import Decimal

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from .finance_models import ChargeObligation
from .finance_reporting import explicit_person_id, explicit_plot_id, obligation_report_queryset, outstanding_amount
from .management.commands.setup_roles import ADMIN, PRIVATE_REGISTRY
from .models import Account, BillingPeriod, Charge, LandPlot, Payment, PaymentAllocation, Person, User
from .resident_models import TsnMembership


class FinanceReportingTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='REPORT-1', plot='Синтетический отчётный участок')
        self.period = BillingPeriod.objects.create(starts=date(2026, 1, 1), ends=date(2027, 1, 1))
        self.charge = Charge.objects.create(
            account=self.account,
            period=self.period,
            kind='service',
            amount=Decimal('900.00'),
            status='approved',
        )
        self.person = Person.objects.create(full_name='Секретный Синтетический Плательщик')
        self.obligation = ChargeObligation.objects.create(
            charge=self.charge,
            category=ChargeObligation.CATEGORY_MEMBERSHIP,
            payer_scope=ChargeObligation.PAYER_PERSON,
            person=self.person,
            due_on=date(2026, 6, 1),
            base_amount=Decimal('1000.00'),
            relief_amount=Decimal('100.00'),
            basis='Синтетическое основание отчёта',
            relief_basis='Синтетическая льгота',
        )

    def test_report_counts_only_confirmed_allocated_payment(self):
        payment = Payment.objects.create(
            account=self.account,
            paid_on=date(2026, 5, 15),
            amount=Decimal('400.00'),
            method='bank',
            status='confirmed',
        )
        PaymentAllocation.objects.create(
            payment=payment,
            charge=self.charge,
            amount=Decimal('350.00'),
        )

        row = obligation_report_queryset().get(pk=self.obligation.pk)

        self.assertEqual(row.confirmed_paid, Decimal('350.00'))
        self.assertEqual(outstanding_amount(row), Decimal('550.00'))

    def test_reporting_dimensions_are_explicit_not_inferred_from_account(self):
        plot = LandPlot.objects.create(label='REPORT-PLOT', account=self.account)

        row = obligation_report_queryset().get(pk=self.obligation.pk)

        self.assertEqual(explicit_person_id(row), self.person.pk)
        self.assertIsNone(explicit_plot_id(row))
        self.assertEqual(plot.account_id, self.account.pk)

    def test_reporting_can_expose_explicit_person_and_plot_pair(self):
        plot = LandPlot.objects.create(label='REPORT-PERSON-PLOT', account=self.account)
        self.obligation.plot = plot
        self.obligation.save()

        row = obligation_report_queryset().get(pk=self.obligation.pk)

        self.assertEqual(explicit_person_id(row), self.person.pk)
        self.assertEqual(explicit_plot_id(row), plot.pk)

    def test_membership_scope_resolves_only_explicit_membership_person(self):
        membership = TsnMembership.objects.create(
            person=self.person,
            application_on=date(2025, 12, 1),
            starts=date(2026, 1, 1),
            decision_ref='Синтетическое решение',
        )
        charge = Charge.objects.create(
            account=self.account,
            period=self.period,
            kind='service',
            amount=Decimal('500.00'),
            status='approved',
        )
        obligation = ChargeObligation.objects.create(
            charge=charge,
            category=ChargeObligation.CATEGORY_MEMBERSHIP,
            payer_scope=ChargeObligation.PAYER_MEMBERSHIP,
            membership=membership,
            due_on=date(2026, 7, 1),
            base_amount=Decimal('500.00'),
            basis='Синтетическое членское обязательство',
        )

        row = obligation_report_queryset().get(pk=obligation.pk)
        self.assertEqual(explicit_person_id(row), self.person.pk)
        self.assertIsNone(explicit_plot_id(row))


class FinanceReportAdminPrivacyTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='ADMIN-REPORT-1')
        self.period = BillingPeriod.objects.create(starts=date(2026, 1, 1), ends=date(2027, 1, 1))
        self.person = Person.objects.create(full_name='Секретное Имя Финансового Отчёта')
        self.plot = LandPlot.objects.create(label='ADMIN-REPORT-PLOT', account=self.account)
        charge = Charge.objects.create(
            account=self.account,
            period=self.period,
            kind='service',
            amount=Decimal('500.00'),
            status='approved',
        )
        self.obligation = ChargeObligation.objects.create(
            charge=charge,
            category=ChargeObligation.CATEGORY_OTHER,
            payer_scope=ChargeObligation.PAYER_PERSON,
            person=self.person,
            plot=self.plot,
            due_on=date(2026, 9, 1),
            base_amount=Decimal('500.00'),
            basis='Синтетическое основание',
        )
        call_command('setup_roles')
        self.admin_user = User.objects.create_user(
            username='finance-report-admin', password='test-password', is_staff=True,
        )
        self.admin_user.groups.add(Group.objects.get(name=ADMIN))
        self.private_user = User.objects.create_user(
            username='finance-report-private', password='test-password', is_staff=True,
        )
        self.private_user.groups.add(
            Group.objects.get(name=ADMIN),
            Group.objects.get(name=PRIVATE_REGISTRY),
        )
        self.client = Client()

    def test_general_administrator_sees_finance_but_not_person_name(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse('admin:water_chargeobligation_changelist'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Конкретное лицо (скрыто)')
        self.assertContains(response, self.plot.label)
        self.assertNotContains(response, self.person.full_name)

        detail = self.client.get(reverse('admin:water_chargeobligation_change', args=[self.obligation.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, self.plot.label)
        self.assertNotContains(detail, self.person.full_name)

    def test_private_registry_permission_reveals_explicit_person_only(self):
        self.client.force_login(self.private_user)
        response = self.client.get(reverse('admin:water_chargeobligation_changelist'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.person.full_name)
        self.assertContains(response, self.plot.label)

    def test_report_is_read_only_even_for_administrator(self):
        self.client.force_login(self.admin_user)
        add_response = self.client.get(reverse('admin:water_chargeobligation_add'))
        change_response = self.client.post(
            reverse('admin:water_chargeobligation_change', args=[self.obligation.pk]),
            {'basis': 'Попытка изменения'},
        )

        self.assertIn(add_response.status_code, (302, 403))
        self.assertEqual(change_response.status_code, 403)
        self.obligation.refresh_from_db()
        self.assertEqual(self.obligation.basis, 'Синтетическое основание')