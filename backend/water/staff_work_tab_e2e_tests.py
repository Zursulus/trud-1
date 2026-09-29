from io import StringIO

from axe_playwright_python.sync_playwright import Axe
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from playwright.sync_api import sync_playwright

from .models import User


class StaffWorkTabBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.manager = User.objects.create_user(username="work-tab-e2e", is_staff=True)
        self.manager.groups.add(Group.objects.get(name="Администратор ТСН"))

    def _session_cookie(self):
        device = TOTPDevice.objects.create(user=self.manager, name="work tab e2e")
        self.client.force_login(self.manager)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        return self.client.cookies[settings.SESSION_COOKIE_NAME].value

    def _exercise_mobile(self, browser, label, session_cookie):
        context = browser.new_context(viewport={"width": 390, "height": 844})
        context.add_cookies([{
            "name": settings.SESSION_COOKIE_NAME,
            "value": session_cookie,
            "url": self.live_server_url,
        }])
        page = context.new_page()
        try:
            response = page.goto(f"{self.live_server_url}/work/search/", wait_until="networkidle")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            self.assertEqual(page.locator('.ws-bottom-nav a[href="/work/tasks/"]').count(), 0)
            more_tab = page.locator('.ws-bottom-nav a[href="/work/more/"]')
            self.assertTrue(more_tab.is_visible())

            response = page.goto(f"{self.live_server_url}/work/tasks/", wait_until="networkidle")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            self.assertTrue(page.get_by_role("heading", name="Рабочая очередь", exact=True).is_visible())
            self.assertEqual(page.locator('.ws-bottom-nav a[href="/work/tasks/"]').count(), 0)
            self.assertTrue(more_tab.is_visible())
            self.assertTrue(page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            ))
            violations = Axe().run(page).response.get("violations", [])
            blocking = [
                violation for violation in violations
                if violation.get("impact") in {"serious", "critical"}
                and any(str(tag).startswith("wcag") for tag in violation.get("tags") or [])
            ]
            self.assertEqual(blocking, [], f"{label}: {blocking}")
        finally:
            context.close()

    def test_mobile_work_tab_chromium_and_webkit(self):
        session_cookie = self._session_cookie()
        with sync_playwright() as playwright:
            for browser_type, label in (
                (playwright.chromium, "chromium mobile"),
                (playwright.webkit, "webkit mobile"),
            ):
                browser = browser_type.launch(headless=True)
                try:
                    self._exercise_mobile(browser, label, session_cookie)
                finally:
                    browser.close()
