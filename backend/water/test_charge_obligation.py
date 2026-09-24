from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from .finance_models import ChargeObligation
from .models import Account, BillingPeriod, Charge, LandPlot, Person, User
from .resident_models import TsnMembership


class ChargeObligationTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='FIN-1', plot='Синтетический участок 1')
        self.period = BillingPeriod.objects.create(
            starts=date(2026, 1, 1),
            ends=date(2027, 1, 1),
        )
        self.charge = Charge.objects.create(
            account=self.account,
            period=self.period,
            kind='service',
            amount=Decimal('900.00'),
            status='draft',
            notes='Синтетическое ежегодное начисление',
        )
        self.staff = User.objects.create_user(username='finance-audit', is_staff=True)

    def obligation(self, **overrides):
        values = {
            'charge': self.charge,
            'category': ChargeObligation.CATEGORY_MEMBERSHIP,
            'payer_scope': ChargeObligation.PAYER_ACCOUNT,
            'due_on': date(2026, 6, 1),
            'base_amount': Decimal('1000.00'),
            'relief_amount': Decimal('100.00'),
            'basis': 'Синтетическое решение для ZUR-54',
            'relief_basis': 'Синтетическая льгота',
        }
        values.update(overrides)
        return ChargeObligation.objects.create(**values)

    def test_account_scope_preserves_existing_charge_and_payment_semantics(self):
        obligation = self.obligation()

        self.assertEqual(obligation.charge_id, self.charge.pk)
        self.assertEqual(obligation.financial_period, self.period)
        self.assertIsNone(obligation.plot_id)
        self.assertIsNone(obligation.person_id)
        self.assertIsNone(obligation.membership_id)
        self.assertEqual(Charge.objects.get(pk=self.charge.pk).amount, Decimal('900.00'))

    def test_plot_scope_requires_plot_from_same_account(self):
        plot = LandPlot.objects.create(label='FIN-PLOT-1', account=self.account)
        obligation = self.obligation(
            payer_scope=ChargeObligation.PAYER_PLOT,
            plot=plot,
        )
        self.assertEqual(obligation.plot_id, plot.pk)

        other_account = Account.objects.create(number='FIN-2')
        wrong_plot = LandPlot.objects.create(label='FIN-PLOT-2', account=other_account)
        second_charge = Charge.objects.create(
            account=self.account,
            period=self.period,
            kind='service',
            amount=Decimal('100.00'),
        )
        with self.assertRaises(ValidationError):
            ChargeObligation.objects.create(
                charge=second_charge,
                category=ChargeObligation.CATEGORY_TARGET,
                payer_scope=ChargeObligation.PAYER_PLOT,
                plot=wrong_plot,
                due_on=date(2026, 7, 1),
                base_amount=Decimal('100.00'),
                basis='Синтетическая проверка связи участка',
            )

    def test_person_and_membership_are_explicit_and_never_inferred(self):
        person = Person.objects.create(full_name='Синтетический Плательщик')
        person_charge = Charge.objects.create(
            account=self.account,
            period=self.period,
            kind='service',
            amount=Decimal('300.00'),
        )
        person_obligation = ChargeObligation.objects.create(
            charge=person_charge,
            category=ChargeObligation.CATEGORY_OTHER,
            payer_scope=ChargeObligation.PAYER_PERSON,
            person=person,
            due_on=date(2026, 8, 1),
            base_amount=Decimal('300.00'),
            basis='Синтетическое персональное обязательство',
        )
        self.assertEqual(person_obligation.person_id, person.pk)
        self.assertIsNone(person_obligation.membership_id)

        membership = TsnMembership.objects.create(
            person=person,
            application_on=date(2025, 12, 1),
            starts=date(2026, 1, 1),
            decision_ref='Синтетическое решение о приёме',
        )
        member_charge = Charge.objects.create(
            account=self.account,
            period=self.period,
            kind='service',
            amount=Decimal('500.00'),
        )
        member_obligation = ChargeObligation.objects.create(
            charge=member_charge,
            category=ChargeObligation.CATEGORY_MEMBERSHIP,
            payer_scope=ChargeObligation.PAYER_MEMBERSHIP,
            membership=membership,
            due_on=date(2026, 9, 1),
            base_amount=Decimal('500.00'),
            basis='Синтетическое членское обязательство',
        )
        self.assertEqual(member_obligation.membership_id, membership.pk)
        self.assertIsNone(member_obligation.person_id)

    def test_person_scope_can_name_explicit_plot_context(self):
        plot = LandPlot.objects.create(label='FIN-PLOT-PERSON', account=self.account)
        first_person = Person.objects.create(full_name='Синтетический Плательщик Один')
        second_person = Person.objects.create(full_name='Синтетический Плательщик Два')

        first = self.obligation(
            payer_scope=ChargeObligation.PAYER_PERSON,
            person=first_person,
            plot=plot,
        )
        second_charge = Charge.objects.create(
            account=self.account,
            period=self.period,
            kind='service',
            amount=Decimal('250.00'),
        )
        second = ChargeObligation.objects.create(
            charge=second_charge,
            category=ChargeObligation.CATEGORY_TARGET,
            payer_scope=ChargeObligation.PAYER_PERSON,
            person=second_person,
            plot=plot,
            due_on=date(2026, 10, 1),
            base_amount=Decimal('250.00'),
            basis='Второе синтетическое обязательство по тому же участку',
        )

        self.assertEqual(first.person_id, first_person.pk)
        self.assertEqual(first.plot_id, plot.pk)
        self.assertEqual(second.person_id, second_person.pk)
        self.assertEqual(second.plot_id, plot.pk)

    def test_membership_scope_can_name_explicit_plot_context(self):
        plot = LandPlot.objects.create(label='FIN-PLOT-MEMBER', account=self.account)
        person = Person.objects.create(full_name='Синтетический Член С Участком')
        membership = TsnMembership.objects.create(
            person=person,
            application_on=date(2025, 12, 1),
            starts=date(2026, 1, 1),
            decision_ref='Синтетическое решение о членстве',
        )
        obligation = self.obligation(
            payer_scope=ChargeObligation.PAYER_MEMBERSHIP,
            membership=membership,
            plot=plot,
        )

        self.assertEqual(obligation.membership_id, membership.pk)
        self.assertEqual(obligation.plot_id, plot.pk)
        self.assertIsNone(obligation.person_id)

    def test_scope_rejects_mixed_subjects(self):
        person = Person.objects.create(full_name='Синтетический Смешанный')
        plot = LandPlot.objects.create(label='FIN-PLOT-MIX', account=self.account)
        with self.assertRaises(ValidationError):
            self.obligation(
                payer_scope=ChargeObligation.PAYER_PLOT,
                plot=plot,
                person=person,
            )

    def test_relief_requires_basis_and_must_match_final_charge_amount(self):
        with self.assertRaises(ValidationError):
            self.obligation(relief_basis='')

        with self.assertRaises(ValidationError):
            self.obligation(relief_amount=Decimal('1100.00'), relief_basis='Ошибка')

        with self.assertRaises(ValidationError):
            self.obligation(base_amount=Decimal('950.00'), relief_amount=Decimal('100.00'))

    def test_negative_adjustment_stays_separate_from_obligation_context(self):
        adjustment = Charge.objects.create(
            account=self.account,
            period=self.period,
            kind='adjustment',
            amount=Decimal('-50.00'),
        )
        with self.assertRaises(ValidationError):
            ChargeObligation.objects.create(
                charge=adjustment,
                category=ChargeObligation.CATEGORY_OTHER,
                payer_scope=ChargeObligation.PAYER_ACCOUNT,
                due_on=date(2026, 10, 1),
                base_amount=Decimal('0.00'),
                relief_amount=Decimal('0.00'),
                basis='Корректировка должна оставаться отдельной записью',
            )

    def test_existing_legacy_charge_needs_no_obligation_row(self):
        legacy = Charge.objects.create(
            account=self.account,
            period=self.period,
            kind='water',
            amount=Decimal('123.45'),
        )
        self.assertFalse(ChargeObligation.objects.filter(charge=legacy).exists())
        legacy.full_clean()

    def test_history_records_changes_with_actor(self):
        obligation = self.obligation()
        obligation._history_user = self.staff
        obligation._change_reason = 'Уточнение срока на синтетике'
        obligation.due_on = date(2026, 6, 15)
        obligation.save()

        self.assertEqual(obligation.history.count(), 2)
        latest = obligation.history.first()
        self.assertEqual(latest.history_user, self.staff)
        self.assertEqual(latest.history_change_reason, 'Уточнение срока на синтетике')