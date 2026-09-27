from io import StringIO

from axe_playwright_python.sync_playwright import Axe
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from playwright.sync_api import sync_playwright

from .access_requests import ResidentAccessRequest
from .models import Account, ResidentAccess, ResidentInvite, User


class StaffWorkspaceAccessBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.staff = User.objects.create_user(username="access-e2e", is_staff=True)
        self.staff.groups.add(
            Group.objects.get(name="Администратор ТСН"),
            Group.objects.get(name="Закрытый реестр членов ТСН"),
        )
        self.account = Account.objects.create(number="ACCESS-E2E", plot="Тестовый участок Access E2E")
        self.chromium_request = self._request("chromium")
        self.webkit_request = self._request("webkit")

    def _request(self, suffix):
        return ResidentAccessRequest.objects.create(
            full_name=f"Заявитель Access {suffix}",
            email=f"access-{suffix}@example.test",
            phone="+7 900 555-44-33",
            plot_hint="Тестовый участок Access E2E",
            claimed_role=ResidentAccessRequest.CLAIM_OWNER,
            message=f"E2E {suffix}",
            submission_key=f"access-e2e-{suffix}",
        )

    def _verified_session_cookie(self):
        device = TOTPDevice.objects.create(user=self.staff, name="access workspace e2e device")
        self.client.force_login(self.staff)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        return self.client.cookies[settings.SESSION_COOKIE_NAME].value

    def _assert_no_blocking_accessibility(self, page, label):
        results = Axe().run(page).response
        blocking = [
            violation for violation in results.get("violations", [])
            if violation.get("impact") in {"serious", "critical"}
            and any(str(tag).startswith("wcag") for tag in violation.get("tags") or [])
        ]
        self.assertEqual(blocking, [], f"{label}: {blocking}")

    def _exercise(self, browser, label, session_cookie, request_id):
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
            response = page.goto(f"{self.live_server_url}/work/access/", wait_until="networkidle")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            self.assertTrue(page.get_by_role("heading", name="Доступ жителей", exact=True).is_visible())
            self.assertEqual(page.locator(".ws-bottom-nav a").count(), 5)

            page.goto(f"{self.live_server_url}/work/access/requests/{request_id}/", wait_until="networkidle")
            self.assertTrue(page.get_by_text("+7 900 555-44-33", exact=True).is_visible())
            page.locator("#id_account").select_option(str(self.account.pk))
            page.locator("#id_role").select_option("owner")
            page.locator("#id_decision_note").fill(f"E2E проверка {label}")
            page.get_by_role("button", name="Одобрить и создать приглашение", exact=True).click()
            page.wait_for_load_state("networkidle")

            self.assertTrue(page.get_by_text("Одноразовая ссылка создана.", exact=True).is_visible())
            self.assertTrue(page.locator("#invite-url").is_visible())
            self.assertTrue(page.locator("#invite-url").input_value())
            self.assertTrue(page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            ))
            self._assert_no_blocking_accessibility(page, label)
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])
        finally:
            context.close()

    def test_access_mobile_chromium_and_webkit(self):
        session_cookie = self._verified_session_cookie()
        chromium_id = self.chromium_request.pk
        webkit_id = self.webkit_request.pk

        with sync_playwright() as playwright:
            chromium = playwright.chromium.launch(headless=True)
            try:
                self._exercise(chromium, "Chromium mobile", session_cookie, chromium_id)
            finally:
                chromium.close()
            webkit = playwright.webkit.launch(headless=True)
            try:
                self._exercise(webkit, "WebKit mobile", session_cookie, webkit_id)
            finally:
                webkit.close()

        self.chromium_request.refresh_from_db()
        self.webkit_request.refresh_from_db()
        self.assertEqual(self.chromium_request.status, ResidentAccessRequest.STATUS_APPROVED)
        self.assertEqual(self.webkit_request.status, ResidentAccessRequest.STATUS_APPROVED)
        self.assertEqual(ResidentInvite.objects.filter(access_request__in=[self.chromium_request, self.webkit_request]).count(), 2)
        self.assertEqual(ResidentAccess.objects.count(), 0)
