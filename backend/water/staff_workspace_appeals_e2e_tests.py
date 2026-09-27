from datetime import timedelta
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

from .models import Account, AppealCategory, ResidentAccess, ResidentAppeal, User


class StaffWorkspaceAppealsBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.manager = User.objects.create_user(username="appeals-hub-e2e", is_staff=True)
        self.manager.groups.add(Group.objects.get(name="Администратор ТСН"))
        self.resident = User.objects.create_user(
            username="private-appeal-author@example.test",
            email="private-appeal-author@example.test",
        )
        account = Account.objects.create(number="AP-E2E-1", plot="Тестовый участок обращений")
        ResidentAccess.objects.create(
            user=self.resident,
            account=account,
            role="owner",
            starts=timezone.localdate() - timedelta(days=30),
        )
        category = AppealCategory.objects.create(name="E2E документы")
        self.appeal = ResidentAppeal.objects.create(
            account=account,
            author=self.resident,
            category=category,
            subject="E2E обращение жителя",
            message="Исходный вопрос для мобильной проверки.",
        )

    def _verified_session_cookie(self):
        device = TOTPDevice.objects.create(user=self.manager, name="appeals hub e2e device")
        self.client.force_login(self.manager)
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

    def _exercise(self, browser, label, session_cookie):
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
            response = page.goto(f"{self.live_server_url}/work/appeals/", wait_until="networkidle")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            self.assertTrue(page.get_by_role("heading", name="Обращения", exact=True).is_visible())
            self.assertEqual(page.get_by_text("private-appeal-author@example.test").count(), 0)
            page.get_by_text("E2E обращение жителя", exact=True).click()
            page.wait_for_url("**/work/appeals/*/")
            page.wait_for_load_state("networkidle")
            self.assertTrue(page.get_by_role("heading", name="E2E обращение жителя", exact=True).is_visible())
            self.assertEqual(page.get_by_text("private-appeal-author@example.test").count(), 0)
            page.locator("#id_body").fill(f"Ответ из {label}.")
            page.locator("#id_next_status").select_option("awaiting_resident")
            page.get_by_role("button", name="Отправить сообщение", exact=True).click()
            page.wait_for_load_state("networkidle")
            self.assertTrue(page.get_by_text("Сообщение отправлено жителю.", exact=True).is_visible())
            self.assertTrue(page.get_by_text(f"Ответ из {label}.", exact=True).is_visible())
            self.assertTrue(page.get_by_text("Нужен ответ жителя", exact=True).first.is_visible())
            self.assertTrue(page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            ))
            self._assert_no_blocking_accessibility(page, label)
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])
        finally:
            context.close()

    def test_appeals_hub_mobile_chromium_and_webkit(self):
        session_cookie = self._verified_session_cookie()
        with sync_playwright() as playwright:
            chromium = playwright.chromium.launch(headless=True)
            try:
                self._exercise(chromium, "Chromium mobile", session_cookie)
            finally:
                chromium.close()
            webkit = playwright.webkit.launch(headless=True)
            try:
                self._exercise(webkit, "WebKit mobile", session_cookie)
            finally:
                webkit.close()
