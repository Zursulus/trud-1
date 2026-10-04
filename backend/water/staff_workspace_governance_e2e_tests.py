import re
from datetime import timedelta
from io import StringIO

from axe_playwright_python.sync_playwright import Axe
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from .board_polls import BoardPoll
from .models import User


class StaffWorkspaceGovernanceBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.staff = User.objects.create_user(username="governance-e2e", is_staff=True)
        self.staff.groups.add(Group.objects.get(name="Администратор ТСН"))

    def _verified_session_cookie(self):
        device = TOTPDevice.objects.create(user=self.staff, name="governance workspace e2e device")
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
            response = page.goto(f"{self.live_server_url}/work/more/", wait_until="networkidle")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            page.get_by_role("link", name="Опросы правления", exact=False).click()
            page.wait_for_load_state("networkidle")
            expect(page.get_by_role("heading", name="Опросы правления", exact=True)).to_be_visible(timeout=10000)
            self.assertTrue(page.get_by_text("неофициальные опросы", exact=False).first.is_visible())

            page.get_by_role("link", name="Новый опрос", exact=True).click()
            page.wait_for_load_state("networkidle")
            title = f"E2E опрос {label}"
            page.locator("#id_title").fill(title)
            page.locator("#id_description").fill("Только предварительная внутренняя сверка")
            future = timezone.localtime(timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
            page.locator("#id_closes_at").fill(future)
            page.locator("#id_questions").fill("Поддержать рабочий вариант?\nПродолжить подготовку?")
            page.get_by_role("button", name="Создать опрос", exact=True).click()
            page.wait_for_url(re.compile(r".*/work/governance/\d+/$"), timeout=10000)
            page.wait_for_load_state("networkidle")

            expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible(timeout=10000)
            self.assertTrue(page.get_by_text("не является общим собранием", exact=False).last.is_visible())
            self.assertTrue(page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            ))
            self._assert_no_blocking_accessibility(page, f"{label} detail")

            page.get_by_role("button", name="Закрыть опрос", exact=True).click()
            page.wait_for_load_state("networkidle")
            expect(page.get_by_text("Завершён", exact=True).first).to_be_visible(timeout=10000)
            expect(page.get_by_role("button", name="Закрыть опрос", exact=True)).not_to_be_visible(timeout=10000)
            self.assertTrue(page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            ))
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])
        finally:
            context.close()

    def test_governance_mobile_chromium_and_webkit(self):
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

        self.assertEqual(BoardPoll.objects.filter(title__startswith="E2E опрос").count(), 2)
