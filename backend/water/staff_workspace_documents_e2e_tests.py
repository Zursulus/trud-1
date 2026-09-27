from io import StringIO

from axe_playwright_python.sync_playwright import Axe
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from django.utils import timezone
from playwright.sync_api import sync_playwright

from public_site.models import PublicNews

from .models import User


class StaffWorkspaceDocumentsBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.staff = User.objects.create_user(username="documents-e2e", is_staff=True)
        self.staff.groups.add(Group.objects.get(name="Администратор ТСН"))

    def _verified_session_cookie(self):
        device = TOTPDevice.objects.create(user=self.staff, name="documents workspace e2e device")
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
            response = page.goto(f"{self.live_server_url}/work/documents/", wait_until="networkidle")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            self.assertTrue(page.get_by_role("heading", name="Документы и контент", exact=True).is_visible())
            for heading in ("Документы жителей", "Публичные документы", "Новости сайта"):
                self.assertTrue(page.get_by_role("heading", name=heading, exact=True).is_visible())
            self.assertTrue(page.get_by_role("link", name="Добавить документ жителя", exact=True).is_visible())
            self.assertTrue(page.get_by_role("link", name="Добавить публичный документ", exact=True).is_visible())
            self.assertTrue(page.get_by_role("link", name="Создать новость", exact=True).is_visible())

            page.get_by_role("link", name="Создать новость", exact=True).click()
            page.wait_for_load_state("networkidle")
            title = f"E2E новость {label}"
            page.locator("#id_title").fill(title)
            page.locator("#id_category").fill("НОВОСТЬ")
            page.locator("#id_summary").fill("Краткий анонс E2E")
            page.locator("#id_body").fill("Проверка мобильного рабочего процесса документов.")
            page.locator("#id_published_on").fill(timezone.localdate().isoformat())
            page.get_by_role("button", name="Сохранить новость", exact=True).click()
            page.wait_for_load_state("networkidle")

            self.assertTrue(page.get_by_role("heading", name=title, exact=True).is_visible())
            self.assertTrue(page.get_by_text("Черновик", exact=True).first.is_visible())
            self.assertTrue(page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            ))
            self._assert_no_blocking_accessibility(page, label)
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])
        finally:
            context.close()

    def test_documents_mobile_chromium_and_webkit(self):
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

        self.assertEqual(PublicNews.objects.filter(title__startswith="E2E новость").count(), 2)
