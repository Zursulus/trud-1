from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from .models import LandPlot, Person, PlotRelation, ResidentAccess, User
from .resident_models import ResidentIdentity, TsnMembership


class ResidentIdentityTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(full_name='Тестовый Житель')
        self.user = User.objects.create_user(username='resident-identity-test')
        self.staff = User.objects.create_user(username='identity-verifier', is_staff=True)

    def test_verified_identity_is_separate_from_account_access(self):
        identity = ResidentIdentity.objects.create(
            user=self.user,
            person=self.person,
            verified_by=self.staff,
            basis='Синтетическая проверка ZUR-68',
        )

        self.assertEqual(identity.version, 1)
        self.assertEqual(identity.history.count(), 1)
        self.assertEqual(ResidentAccess.objects.filter(user=self.user).count(), 0)
        self.assertEqual(self.user.resident_identity.person_id, self.person.pk)

    def test_one_person_cannot_be_bound_to_two_resident_logins(self):
        ResidentIdentity.objects.create(
            user=self.user,
            person=self.person,
            verified_by=self.staff,
            basis='Первичная тестовая связь',
        )
        another = User.objects.create_user(username='another-resident-identity-test')

        with self.assertRaises(ValidationError):
            ResidentIdentity.objects.create(
                user=another,
                person=self.person,
                verified_by=self.staff,
                basis='Конфликтующая тестовая связь',
            )

    def test_staff_login_cannot_become_resident_identity(self):
        with self.assertRaises(ValidationError):
            ResidentIdentity.objects.create(
                user=self.staff,
                person=self.person,
                verified_by=self.staff,
                basis='Недопустимая тестовая связь',
            )

    def test_identity_verifier_must_be_staff(self):
        verifier = User.objects.create_user(username='not-a-verifier')
        with self.assertRaises(ValidationError):
            ResidentIdentity.objects.create(
                user=self.user,
                person=self.person,
                verified_by=verifier,
                basis='Недопустимый проверяющий',
            )


class TsnMembershipTests(TestCase):
    def setUp(self):
        self.person = Person.objects.create(full_name='Тестовый Член ТСН')
        self.today = timezone.localdate()

    def _membership(self, **overrides):
        values = {
            'person': self.person,
            'application_on': self.today - timedelta(days=40),
            'starts': self.today - timedelta(days=30),
            'decision_ref': 'Синтетическое решение правления ZUR-68',
        }
        values.update(overrides)
        return TsnMembership(**values)

    def test_membership_is_independent_from_plot_relation_and_portal_access(self):
        plot = LandPlot.objects.create(label='TEST-ZUR68')
        PlotRelation.objects.create(
            person=self.person,
            plot=plot,
            role=PlotRelation.OWNER,
            starts=self.today - timedelta(days=100),
            document='Синтетическое основание',
        )

        self.assertFalse(TsnMembership.objects.filter(person=self.person).exists())
        self.assertEqual(ResidentAccess.objects.count(), 0)

        membership = self._membership()
        membership.save()
        self.assertTrue(membership.is_active)
        self.assertEqual(ResidentAccess.objects.count(), 0)

    def test_membership_cannot_start_before_application(self):
        membership = self._membership(
            application_on=self.today,
            starts=self.today - timedelta(days=1),
        )
        with self.assertRaises(ValidationError):
            membership.save()

    def test_closed_membership_requires_end_reason(self):
        membership = self._membership(ends=self.today)
        with self.assertRaises(ValidationError):
            membership.save()

    def test_membership_periods_cannot_overlap_but_rejoin_is_allowed(self):
        first = self._membership()
        first.save()
        first.ends = self.today
        first.end_reason = TsnMembership.END_VOLUNTARY
        first.end_document = 'Синтетическое заявление о выходе'
        first.save()

        overlapping = self._membership(
            application_on=self.today - timedelta(days=10),
            starts=self.today - timedelta(days=5),
            decision_ref='Пересекающееся решение',
            ends=self.today + timedelta(days=5),
            end_reason=TsnMembership.END_VOLUNTARY,
        )
        with self.assertRaises(ValidationError):
            overlapping.save()

        rejoined = self._membership(
            application_on=self.today,
            starts=self.today + timedelta(days=1),
            decision_ref='Синтетическое повторное решение',
        )
        rejoined.save()

        self.assertEqual(TsnMembership.objects.filter(person=self.person).count(), 2)
        self.assertEqual(first.history.count(), 2)
        self.assertEqual(rejoined.history.count(), 1)
