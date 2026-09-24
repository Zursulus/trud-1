from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Account, AppealCategory, LandPlot, Meter, Person, PlotRelation, Reading,
    ResidentAccess, ResidentAppeal, SupplyNode, User,
)
from .portal import has_any_portal_access, issue_password_reset
from .portal_permissions import (
    CAP_APPEALS,
    CAP_DOCUMENTS,
    CAP_FINANCE,
    CAP_REPRESENT,
    CAP_SUBMIT_WATER,
    CAP_VIEW_ACCOUNT,
    PortalGrant,
    resolved_access,
    resolved_accesses,
)
from .resident_models import ResidentIdentity, TsnMembership


class PortalPermissionResolverTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.account = Account.objects.create(number='PERM-1', plot='Тестовая 1')
        self.user = User.objects.create_user(
            username='portal-permissions@example.test',
            email='portal-permissions@example.test',
            password='test-password-2026',
        )
        self.person = Person.objects.create(full_name='Тестовый Пользователь Прав')
        self.staff = User.objects.create_user(username='portal-verifier', is_staff=True)

    def _identity(self):
        return ResidentIdentity.objects.create(
            user=self.user,
            person=self.person,
            verified_by=self.staff,
            basis='Синтетическая проверка ZUR-70',
        )

    def _grant(self, **overrides):
        values = {
            'person': self.person,
            'account': self.account,
            'starts': self.today - timedelta(days=1),
            'basis': 'Синтетическое проверенное основание ZUR-70',
            'verified_by': self.staff,
        }
        values.update(overrides)
        grant = PortalGrant(**values)
        grant.save()
        return grant

    def test_legacy_resident_access_keeps_current_workspace(self):
        ResidentAccess.objects.create(
            user=self.user,
            account=self.account,
            role='payer',
            starts=self.today - timedelta(days=1),
        )

        access = resolved_access(self.user, self.account.pk)
        self.assertIsNotNone(access)
        self.assertEqual(access.source, 'legacy')
        self.assertTrue(access.allows(CAP_VIEW_ACCOUNT))
        self.assertTrue(access.allows(CAP_FINANCE))
        self.assertTrue(access.allows(CAP_SUBMIT_WATER))
        self.assertTrue(access.allows(CAP_DOCUMENTS))
        self.assertTrue(access.allows(CAP_APPEALS))
        self.assertFalse(access.allows(CAP_REPRESENT))

    def test_explicit_grant_replaces_broad_legacy_access_instead_of_unioning(self):
        self._identity()
        ResidentAccess.objects.create(
            user=self.user,
            account=self.account,
            role='owner',
            starts=self.today - timedelta(days=10),
        )
        self._grant(can_view_account=True)

        access = resolved_access(self.user, self.account.pk)
        self.assertEqual(access.source, 'grant')
        self.assertTrue(access.can_view_account)
        self.assertFalse(access.can_view_finance)
        self.assertFalse(access.can_submit_water)
        self.assertFalse(access.can_view_documents)
        self.assertFalse(access.can_use_appeals)
        self.assertFalse(access.can_represent)
        self.assertIsNone(resolved_access(self.user, self.account.pk, CAP_FINANCE))

    def test_identity_ownership_and_membership_do_not_create_portal_access(self):
        self._identity()
        plot = LandPlot.objects.create(label='PERM-PLOT-1', account=self.account)
        PlotRelation.objects.create(
            person=self.person,
            plot=plot,
            role=PlotRelation.OWNER,
            starts=self.today - timedelta(days=100),
            document='Синтетическое основание',
        )
        TsnMembership.objects.create(
            person=self.person,
            application_on=self.today - timedelta(days=40),
            starts=self.today - timedelta(days=30),
            decision_ref='Синтетическое решение правления',
        )

        self.assertEqual(resolved_accesses(self.user), [])
        self.assertFalse(has_any_portal_access(self.user))

    def test_explicit_grant_supports_login_and_password_reset_without_legacy_access(self):
        self._identity()
        self._grant(can_view_account=True)

        self.assertTrue(has_any_portal_access(self.user))
        reset, raw = issue_password_reset(self.user, actor=self.staff)
        self.assertTrue(raw)
        self.assertEqual(reset.user_id, self.user.pk)
        self.assertEqual(ResidentAccess.objects.filter(user=self.user).count(), 0)

    def test_grants_cannot_overlap_and_verifier_must_be_staff(self):
        self._identity()
        self._grant(can_view_account=True)
        with self.assertRaises(ValidationError):
            self._grant(
                starts=self.today,
                ends=self.today + timedelta(days=10),
                can_view_account=True,
            )

        other_account = Account.objects.create(number='PERM-2', plot='Тестовая 2')
        outsider = User.objects.create_user(username='not-staff-verifier')
        with self.assertRaises(ValidationError):
            PortalGrant.objects.create(
                person=self.person,
                account=other_account,
                starts=self.today,
                can_view_account=True,
                basis='Недопустимый проверяющий',
                verified_by=outsider,
            )

    def test_special_capability_requires_basic_account_view(self):
        self._identity()
        with self.assertRaises(ValidationError):
            self._grant(can_view_account=False, can_view_finance=True)


class PortalPermissionRouteTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.account = Account.objects.create(number='ROUTE-1', plot='Маршрутная 1')
        self.node = SupplyNode.objects.create(name='Маршрутный тестовый узел')
        self.meter = Meter.objects.create(
            serial='ROUTE-METER-1', kind='individual', node=self.node, account=self.account,
        )
        Reading.objects.create(
            meter=self.meter,
            date=self.today - timedelta(days=1),
            value=Decimal('10.000'),
        )
        self.user = User.objects.create_user(
            username='route-permissions@example.test',
            email='route-permissions@example.test',
            password='test-password-2026',
        )
        self.person = Person.objects.create(full_name='Тестовый Маршрут Пользователя')
        self.staff = User.objects.create_user(username='route-verifier', is_staff=True)
        ResidentIdentity.objects.create(
            user=self.user,
            person=self.person,
            verified_by=self.staff,
            basis='Синтетическая проверка маршрута',
        )
        self.client.force_login(self.user)

    def _grant(self, **overrides):
        values = {
            'person': self.person,
            'account': self.account,
            'starts': self.today - timedelta(days=1),
            'basis': 'Синтетическая проверка маршрута',
            'verified_by': self.staff,
        }
        values.update(overrides)
        return PortalGrant.objects.create(**values)

    def test_view_only_grant_hides_sensitive_sections_and_blocks_direct_urls(self):
        self._grant(can_view_account=True)

        dashboard = self.client.get(reverse('resident_account', args=[self.account.pk]))
        water = self.client.get(reverse('resident_water', args=[self.account.pk]))
        payments = self.client.get(reverse('resident_payments', args=[self.account.pk]))
        documents = self.client.get(reverse('resident_documents', args=[self.account.pk]))
        appeals = self.client.get(reverse('resident_appeals', args=[self.account.pk]))
        submit = self.client.post(
            reverse('resident_reading', args=[self.account.pk, self.meter.pk]),
            {'date': self.today.isoformat(), 'value': '11.000', 'notes': ''},
        )

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(water.status_code, 200)
        self.assertContains(water, 'просмотр истории без права передачи', html=False)
        self.assertNotContains(dashboard, 'Баланс и оплата')
        self.assertNotContains(dashboard, 'Написать в правление')
        self.assertEqual(payments.status_code, 404)
        self.assertEqual(documents.status_code, 404)
        self.assertEqual(appeals.status_code, 404)
        self.assertEqual(submit.status_code, 404)
        self.assertEqual(Reading.objects.filter(meter=self.meter).count(), 1)

    def test_granted_capabilities_open_only_their_routes(self):
        self._grant(
            can_view_account=True,
            can_view_finance=True,
            can_submit_water=True,
            can_view_documents=True,
            can_use_appeals=False,
        )

        self.assertEqual(self.client.get(reverse('resident_payments', args=[self.account.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('resident_documents', args=[self.account.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('resident_appeals', args=[self.account.pk])).status_code, 404)

        submit = self.client.post(
            reverse('resident_reading', args=[self.account.pk, self.meter.pk]),
            {'date': self.today.isoformat(), 'value': '11.000', 'notes': 'Синтетический ввод'},
        )
        self.assertEqual(submit.status_code, 302)
        self.assertEqual(Reading.objects.filter(meter=self.meter).count(), 2)

    def test_explicit_appeal_grant_works_without_legacy_access(self):
        self._grant(can_view_account=True, can_use_appeals=True)
        category = AppealCategory.objects.create(name='Тестовая тема ZUR-70')

        response = self.client.post(
            reverse('resident_appeal_new', args=[self.account.pk]),
            {
                'category': category.pk,
                'subject': 'Синтетическое обращение',
                'message': 'Проверка explicit-only доступа без ResidentAccess.',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ResidentAccess.objects.filter(user=self.user).count(), 0)
        appeal = ResidentAppeal.objects.get(author=self.user, account=self.account)
        self.assertEqual(appeal.subject, 'Синтетическое обращение')

    def test_legacy_user_routes_remain_available(self):
        legacy_user = User.objects.create_user(username='legacy-route@example.test')
        ResidentAccess.objects.create(
            user=legacy_user,
            account=self.account,
            role='owner',
            starts=self.today - timedelta(days=1),
        )
        self.client.force_login(legacy_user)

        self.assertEqual(self.client.get(reverse('resident_account', args=[self.account.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('resident_payments', args=[self.account.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('resident_water', args=[self.account.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('resident_documents', args=[self.account.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('resident_appeals', args=[self.account.pk])).status_code, 200)
