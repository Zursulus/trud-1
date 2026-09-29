from datetime import timedelta
from decimal import Decimal
from io import StringIO

from axe_playwright_python.sync_playwright import Axe
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from playwright.sync_api import sync_playwright
from django.utils import timezone

from .models import Account, BillingPeriod, BillingPolicy, Charge, Payment, PaymentAllocation, User


class StaffWorkspaceFinanceBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.manager = User.objects.create_user(username="finance-hub-e2e", is_staff=True)
        self.manager.groups.add(Group.objects.get(name="Администратор ТСН"))
        self.account = Account.objects.create(
            number="FIN-E2E-1",
            plot="Тестовый финансовый участок",
            contact_name="Секретный E2E Финконтакт",
            phone="+79996660000",
        )
        today = timezone.localdate()
        self.period = BillingPeriod.objects.create(
            starts=today - timedelta(days=30), ends=today, status="open",
        )
        BillingPolicy.objects.create(
            name="Finance E2E policy", is_default=True, missing_reading="zero", payment_allocation="oldest",
        )

    def _verified_session_cookie(self):
        device = TOTPDevice.objects.create(user=self.manager, name="finance hub e2e device")
        self.client.force_login(self.manager)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        return self.client.cookies[settings.SESSION_COOKIE_NAME].value

    def _finance_case(self, label):
        charge = Charge.objects.create(
            account=self.account,
            period=self.period,
            kind="service",
            amount=Decimal("25.00"),
            status="approved",
            origin="manual",
            notes=f"E2E долг {label}",
        )
        payment = Payment.objects.create(
            account=self.account,
            paid_on=timezone.localdate(),
            amount=Decimal("25.00"),
            method="bank",
            status="pending",
            reference=f"E2E {label}",
        )
        return charge.pk, payment.pk

    def _assert_no_blocking_accessibility(self, page, label):
        results = Axe().run(page).response
        blocking = [
            violation for violation in results.get("violations", [])
            if violation.get("impact") in {"serious", "critical"}
            and any(str(tag).startswith("wcag") for tag in violation.get("tags") or [])
        ]
        self.assertEqual(blocking, [], f"{label}: {blocking}")

    def _exercise(self, browser, label, session_cookie, payment_id):
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_cookies([{
            "name": settings.SESSION_COOKIE_NAME,
            "value": session_cookie,
            "url": self.live_server_url,
        }])
        page = context.new_page()
        page_errors = []
        console_errors = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        try:
            response = page.goto(f"{self.live_server_url}/work/finance/", wait_until="networkidle")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            self.assertTrue(page.get_by_role("heading", name="Финансы", exact=True).is_visible())
            self.assertEqual(page.get_by_text("Секретный E2E Финконтакт").count(), 0)
            self.assertEqual(page.get_by_text("+79996660000").count(), 0)
            self.assertEqual(page.locator(".ws-bottom-nav a").count(), 3)

            page.goto(f"{self.live_server_url}/work/finance/payments/{payment_id}/", wait_until="networkidle")
            page.get_by_role("button", name="Подтвердить оплату", exact=True).click()
            page.wait_for_load_state("networkidle")
            self.assertTrue(page.get_by_text("Оплата подтверждена.", exact=True).is_visible())
            page.get_by_role("button", name="Распределить по правилам", exact=True).click()
            page.wait_for_load_state("networkidle")
            self.assertTrue(page.get_by_text("Распределено", exact=True).first.is_visible())
            self.assertTrue(page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            ))
            self._assert_no_blocking_accessibility(page, label)
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])
        finally:
            context.close()

    def test_finance_mobile_chromium_and_webkit(self):
        session_cookie = self._verified_session_cookie()
        chromium_charge_id, chromium_payment_id = self._finance_case("Chromium mobile")
        webkit_charge_id, webkit_payment_id = self._finance_case("WebKit mobile")

        with sync_playwright() as playwright:
            chromium = playwright.chromium.launch(headless=True)
            try:
                self._exercise(chromium, "Chromium mobile", session_cookie, chromium_payment_id)
            finally:
                chromium.close()
            webkit = playwright.webkit.launch(headless=True)
            try:
                self._exercise(webkit, "WebKit mobile", session_cookie, webkit_payment_id)
            finally:
                webkit.close()

        self.assertTrue(PaymentAllocation.objects.filter(
            payment_id=chromium_payment_id, charge_id=chromium_charge_id,
        ).exists())
        self.assertTrue(PaymentAllocation.objects.filter(
            payment_id=webkit_payment_id, charge_id=webkit_charge_id,
        ).exists())
