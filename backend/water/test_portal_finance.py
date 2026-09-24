from datetime import date
from decimal import Decimal

from django.test import TestCase

from .finance_models import ChargeObligation
from .finance_reporting import account_charge_rows
from .models import Account, BillingPeriod, Charge, Payment, PaymentAllocation, Person, ResidentAccess, User
from .resident_numbers import ResidentNumberSlot


class ResidentFinanceContextTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='PORTAL-FIN-1', plot='Синтетический участок 1')
        self.other_account = Account.objects.create(number='PORTAL-FIN-2', plot='Чужой синтетический участок')
        self.period = BillingPeriod.objects.create(
            starts=date(2026, 1, 1),
            ends=date(2027, 1, 1),
        )
        self.user = User.objects.create_user(
            username='portal-finance@example.test',
            password='test-password',
        )
        slot = ResidentNumberSlot.objects.get(pk=128)
        slot.user = self.user
        slot.save()
        ResidentAccess.objects.create(
            user=self.user,
            account=self.account,
            role='owner',
            starts=date(2026, 1, 1),
        )

    def _charge(self, amount, *, status='approved'):
        return Charge.objects.create(
            account=self.account,
            period=self.period,
            kind='service',
            amount=Decimal(amount),
            status=status,
        )

    def _confirmed_allocation(self, charge, amount):
        payment = Payment.objects.create(
            account=self.account,
            paid_on=date(2026, 5, 15),
            amount=Decimal(amount),
            method='bank',
            status='confirmed',
        )
        PaymentAllocation.objects.create(
            payment=payment,
            charge=charge,
            amount=Decimal(amount),
        )

    def test_account_charge_rows_use_each_charge_allocations(self):
        paid = self._charge('900.00')
        self._confirmed_allocation(paid, '900.00')

        partial = self._charge('400.00')
        self._confirmed_allocation(partial, '150.00')

        overdue = self._charge('500.00')
        ChargeObligation.objects.create(
            charge=overdue,
            category=ChargeObligation.CATEGORY_TARGET,
            payer_scope=ChargeObligation.PAYER_ACCOUNT,
            due_on=date(2026, 5, 1),
            base_amount=Decimal('500.00'),
            basis='Синтетический целевой взнос',
        )

        rows = {row['charge_id']: row for row in account_charge_rows(self.account, on_date=date(2026, 9, 24))}

        self.assertEqual(rows[paid.pk]['state'], 'paid')
        self.assertEqual(rows[paid.pk]['outstanding'], Decimal('0.00'))
        self.assertEqual(rows[partial.pk]['state'], 'partial')
        self.assertEqual(rows[partial.pk]['outstanding'], Decimal('250.00'))
        self.assertEqual(rows[overdue.pk]['state'], 'overdue')
        self.assertEqual(rows[overdue.pk]['outstanding'], Decimal('500.00'))
        self.assertEqual(rows[overdue.pk]['title'], 'Целевой взнос')

    def test_person_obligation_never_exposes_person_name_to_finance_portal(self):
        secret_person = Person.objects.create(full_name='Секретное Имя Плательщика')
        charge = self._charge('900.00')
        ChargeObligation.objects.create(
            charge=charge,
            category=ChargeObligation.CATEGORY_MEMBERSHIP,
            payer_scope=ChargeObligation.PAYER_PERSON,
            person=secret_person,
            due_on=date(2026, 12, 1),
            base_amount=Decimal('1000.00'),
            relief_amount=Decimal('100.00'),
            relief_basis='Синтетическая льгота',
            basis='Синтетическое членское начисление',
        )
        self._confirmed_allocation(charge, '900.00')

        self.client.force_login(self.user)
        response = self.client.get(f'/admin/cabinet/account/{self.account.pk}/payments/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Членский взнос')
        self.assertContains(response, 'Персональное обязательство')
        self.assertContains(response, 'Льгота 100')
        self.assertContains(response, 'Оплачено')
        self.assertNotContains(response, secret_person.full_name)

    def test_partial_overdue_and_legacy_charge_render_independently(self):
        partial = self._charge('400.00')
        ChargeObligation.objects.create(
            charge=partial,
            category=ChargeObligation.CATEGORY_OTHER,
            payer_scope=ChargeObligation.PAYER_ACCOUNT,
            due_on=date(2026, 12, 1),
            base_amount=Decimal('400.00'),
            basis='Синтетическое прочее начисление',
        )
        self._confirmed_allocation(partial, '150.00')

        overdue = self._charge('500.00')
        ChargeObligation.objects.create(
            charge=overdue,
            category=ChargeObligation.CATEGORY_TARGET,
            payer_scope=ChargeObligation.PAYER_ACCOUNT,
            due_on=date(2026, 5, 1),
            base_amount=Decimal('500.00'),
            basis='Синтетический просроченный взнос',
        )

        legacy = self._charge('75.00')

        foreign_charge = Charge.objects.create(
            account=self.other_account,
            period=self.period,
            kind='service',
            amount=Decimal('9999.00'),
            status='approved',
        )

        self.client.force_login(self.user)
        response = self.client.get(f'/admin/cabinet/account/{self.account.pk}/payments/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Остаток 250')
        self.assertContains(response, 'Просрочено · остаток 500')
        self.assertContains(response, 'Услуга / иной платёж')
        self.assertNotContains(response, '9999')
        self.assertNotContains(response, f'charge-{foreign_charge.pk}')
