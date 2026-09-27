from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .models import (
    Account,
    BillingPeriod,
    BillingPolicy,
    Charge,
    Payment,
    PaymentAllocation,
    Tariff,
    User,
)


class StaffWorkspaceFinanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.manager = User.objects.create_user(username="finance-manager", is_staff=True)
        cls.manager.groups.add(Group.objects.get(name="Администратор ТСН"))
        cls.operator = User.objects.create_user(username="finance-operator", is_staff=True)
        cls.operator.groups.add(Group.objects.get(name="Оператор воды"))
        cls.private_user = User.objects.create_user(username="finance-private", is_staff=True)
        cls.private_user.groups.add(Group.objects.get(name="Закрытый реестр членов ТСН"))
        cls.viewer = User.objects.create_user(username="finance-viewer", is_staff=True)
        for codename in (
            "view_account", "view_billingperiod", "view_charge", "view_payment", "view_paymentallocation",
        ):
            cls.viewer.user_permissions.add(Permission.objects.get(content_type__app_label="water", codename=codename))

        cls.account = Account.objects.create(
            number="FIN-101",
            plot="Финансовая 101",
            contact_name="Секретный Финансовый Контакт",
            phone="+79995550101",
        )
        today = timezone.localdate()
        cls.period = BillingPeriod.objects.create(
            starts=today - timedelta(days=30),
            ends=today,
            status="open",
        )
        BillingPolicy.objects.create(
            name="Finance default",
            is_default=True,
            missing_reading="norm",
            monthly_norm_m3=Decimal("5.000"),
            payment_allocation="oldest",
        )
        Tariff.objects.create(
            name="Finance common",
            rate=Decimal("10.0000"),
            starts=cls.period.starts,
        )

    def login(self, user):
        self.client.force_login(user)

    def _calculate_and_approve_charge(self):
        self.login(self.manager)
        response = self.client.post(
            f"/work/finance/periods/{self.period.pk}/",
            {"action": "calculate"},
        )
        self.assertEqual(response.status_code, 302)
        charge = Charge.objects.get(period=self.period, account=self.account)
        self.assertEqual(charge.status, "draft")
        self.assertEqual(charge.amount, Decimal("50.00"))
        response = self.client.post(
            f"/work/finance/periods/{self.period.pk}/",
            {"action": "approve_charge", "charge_id": charge.pk},
        )
        self.assertEqual(response.status_code, 302)
        charge.refresh_from_db()
        self.assertEqual(charge.status, "approved")
        return charge

    def test_manager_finance_hub_hides_contact_pii_and_account_links_to_workspace(self):
        self.login(self.manager)
        response = self.client.get("/work/finance/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Финансы")
        self.assertNotContains(response, "Секретный Финансовый Контакт")
        self.assertNotContains(response, "+79995550101")

        account = self.client.get(f"/work/accounts/{self.account.pk}/")
        self.assertContains(account, f"/work/finance/accounts/{self.account.pk}/")
        self.assertNotContains(account, "Секретный Финансовый Контакт")

    def test_non_finance_roles_are_denied_and_viewer_is_read_only(self):
        for user in (self.operator, self.private_user):
            self.login(user)
            self.assertEqual(self.client.get("/work/finance/").status_code, 403)
            self.assertEqual(self.client.get("/work/finance/payments/").status_code, 403)

        self.login(self.viewer)
        response = self.client.get(f"/work/finance/periods/{self.period.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Рассчитать / пересчитать черновики")
        post = self.client.post(f"/work/finance/periods/{self.period.pk}/", {"action": "calculate"})
        self.assertEqual(post.status_code, 403)

    def test_period_lifecycle_requires_review_of_drafts(self):
        self.login(self.manager)
        response = self.client.post(f"/work/finance/periods/{self.period.pk}/", {"action": "calculate"})
        self.assertEqual(response.status_code, 302)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, "calculated")
        charge = Charge.objects.get(period=self.period, account=self.account)
        self.assertEqual(charge.status, "draft")

        response = self.client.post(f"/work/finance/periods/{self.period.pk}/", {"action": "approve_period"}, follow=True)
        self.assertContains(response, "Сначала утвердите или отмените все черновики")
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, "calculated")

        self.client.post(
            f"/work/finance/periods/{self.period.pk}/",
            {"action": "approve_charge", "charge_id": charge.pk},
        )
        response = self.client.post(f"/work/finance/periods/{self.period.pk}/", {"action": "approve_period"})
        self.assertEqual(response.status_code, 302)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, "approved")

        response = self.client.post(f"/work/finance/periods/{self.period.pk}/", {"action": "close_period"})
        self.assertEqual(response.status_code, 302)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, "closed")

    def test_payment_capture_confirm_allocate_and_reverse_preserves_history(self):
        charge = self._calculate_and_approve_charge()
        self.login(self.manager)
        response = self.client.post(
            "/work/finance/payments/new/",
            {
                "account": self.account.pk,
                "paid_on": timezone.localdate().isoformat(),
                "amount": "50.00",
                "method": "bank",
                "reference": "Оплата воды",
                "notes": "Тестовая оплата",
            },
        )
        self.assertEqual(response.status_code, 302)
        payment = Payment.objects.get(account=self.account)
        self.assertEqual(payment.status, "pending")

        response = self.client.post(f"/work/finance/payments/{payment.pk}/", {"action": "confirm"})
        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "confirmed")

        response = self.client.post(f"/work/finance/payments/{payment.pk}/", {"action": "allocate"})
        self.assertEqual(response.status_code, 302)
        allocation = PaymentAllocation.objects.get(payment=payment)
        self.assertEqual(allocation.charge, charge)
        self.assertEqual(allocation.amount, Decimal("50.00"))

        finance_card = self.client.get(f"/work/finance/accounts/{self.account.pk}/")
        self.assertContains(finance_card, "0,00 ₽")
        self.assertNotContains(finance_card, "Секретный Финансовый Контакт")

        response = self.client.post(f"/work/finance/payments/{payment.pk}/", {"action": "reverse"})
        self.assertEqual(response.status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "reversed")
        self.assertTrue(PaymentAllocation.objects.filter(pk=allocation.pk).exists())
        finance_card = self.client.get(f"/work/finance/accounts/{self.account.pk}/")
        self.assertContains(finance_card, "50,00 ₽")

    def test_archived_account_cannot_receive_new_payment(self):
        archived = Account.objects.create(number="FIN-ARCH", plot="Архив", archived=True)
        self.login(self.manager)
        response = self.client.post(
            "/work/finance/payments/new/",
            {
                "account": archived.pk,
                "paid_on": timezone.localdate().isoformat(),
                "amount": "10.00",
                "method": "bank",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Payment.objects.filter(account=archived).exists())

    def test_finance_pages_have_bounded_query_counts(self):
        self.login(self.manager)
        with CaptureQueriesContext(connection) as dashboard_queries:
            response = self.client.get("/work/finance/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(dashboard_queries), 24, len(dashboard_queries))

        with CaptureQueriesContext(connection) as period_queries:
            response = self.client.get(f"/work/finance/periods/{self.period.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(period_queries), 20, len(period_queries))

        with CaptureQueriesContext(connection) as payment_queries:
            response = self.client.get("/work/finance/payments/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(payment_queries), 20, len(payment_queries))
