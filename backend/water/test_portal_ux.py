from datetime import date
from decimal import Decimal

from django.test import TestCase

from .models import (
    Account, AppealCategory, BillingPeriod, Charge, Meter, Reading, ResidentAccess,
    SupplyNode, User,
)


class ResidentPortalUXTests(TestCase):
    password = 'resident-ux-password-2026!'

    def setUp(self):
        self.account = Account.objects.create(number='UX-1', plot='Садовая, 42')
        self.other = Account.objects.create(number='UX-2', plot='Центральная, 58')
        self.user = User.objects.create_user(
            username='ux-resident@example.test', email='ux-resident@example.test', password=self.password,
        )
        ResidentAccess.objects.create(user=self.user, account=self.account, role='owner', starts=date(2026, 1, 1))
        self.node = SupplyNode.objects.create(name='UX узел')
        self.meter = Meter.objects.create(serial='UX-METER', kind='individual', node=self.node, account=self.account)
        Reading.objects.create(meter=self.meter, date=date(2026, 9, 1), value=Decimal('184.600'))
        period = BillingPeriod.objects.create(starts=date(2026, 9, 1), ends=date(2026, 10, 1))
        Charge.objects.create(account=self.account, period=period, kind='service', amount=Decimal('3200'), status='approved')
        AppealCategory.objects.create(name='Общее', active=True)

    def login(self):
        self.client.force_login(self.user)

    def test_dashboard_is_human_and_does_not_expose_unlinked_plot(self):
        self.login()
        response = self.client.get('/admin/cabinet/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Что требует внимания')
        self.assertContains(response, 'К оплате 3200')
        self.assertContains(response, '184,600')
        self.assertContains(response, 'Садовая, 42')
        self.assertNotContains(response, 'Центральная, 58')

    def test_all_primary_mobile_routes_require_matching_access(self):
        self.login()
        for suffix in ('payments/', 'water/', 'appeals/', 'documents/', 'notifications/', 'more/', 'profile/', 'security/'):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.client.get(f'/admin/cabinet/account/{self.account.pk}/{suffix}').status_code, 200)
                self.assertEqual(self.client.get(f'/admin/cabinet/account/{self.other.pk}/{suffix}').status_code, 404)

    def test_plots_page_lists_only_allowed_accounts(self):
        self.login()
        response = self.client.get('/admin/cabinet/plots/')
        self.assertContains(response, 'Садовая, 42')
        self.assertNotContains(response, 'Центральная, 58')
        ResidentAccess.objects.create(user=self.user, account=self.other, role='representative', starts=date(2026, 1, 1))
        response = self.client.get('/admin/cabinet/plots/')
        self.assertContains(response, 'Центральная, 58')
        self.assertContains(response, 'Представитель')

    def test_login_explains_technical_cookies_without_consent_wall(self):
        response = self.client.get('/admin/cabinet/login/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'только необходимые технические cookies')
        self.assertNotContains(response, 'Принять все cookies')

    def test_security_page_keeps_cookie_policy_and_password_action_clear(self):
        self.login()
        response = self.client.get(f'/admin/cabinet/account/{self.account.pk}/security/')
        self.assertContains(response, 'Сменить пароль')
        self.assertContains(response, 'технические cookies')
        self.assertContains(response, 'Рекламные пиксели')
