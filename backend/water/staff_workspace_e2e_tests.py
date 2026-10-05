from datetime import timedelta
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from axe_playwright_python.sync_playwright import Axe
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from playwright.sync_api import sync_playwright
from django.utils import timezone

from .models import Account, LandPlot, Membership, Meter, SupplyNode, User, WaterGroup


class StaffWorkspaceBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        today = timezone.localdate()
        self.manager = User.objects.create_user(username="workspace-e2e", is_staff=True)
        self.manager.groups.add(Group.objects.get(name="Администратор ТСН"))
        self.account = Account.objects.create(
            number="E2E-101",
            plot="Садовая 101",
            contact_name="Секретный E2E Контакт",
            phone="+79990000999",
        )
        LandPlot.objects.create(label="Участок E2E 101", address="Садовая 101", account=self.account)
        node = SupplyNode.objects.create(name="E2E узел workspace")
        group = WaterGroup.objects.create(name="E2E линия workspace", node=node)
        Membership.objects.create(account=self.account, group=group, starts=today - timedelta(days=30))
        Meter.objects.create(serial="E2E-METER-101", kind="individual", node=node, account=self.account)

    def _verified_session_cookie(self):
        device = TOTPDevice.objects.create(user=self.manager, name="workspace e2e device")
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

    def _exercise(self, browser, viewport, label, session_cookie):
        context = browser.new_context(viewport=viewport)
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
            response = page.goto(f"{self.live_server_url}/work/", wait_until="networkidle")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            self.assertTrue(page.get_by_role("heading", name="Что нужно сделать").is_visible())
            self._assert_no_blocking_accessibility(page, f"{label} home")

            if viewport["width"] <= 640:
                bottom_nav = page.locator(".ws-bottom-nav")
                self.assertEqual(bottom_nav.get_by_role("link").count(), 3)
                self.assertEqual(bottom_nav.get_by_role("link", name="Работа", exact=True).count(), 0)
                self.assertEqual(bottom_nav.get_by_role("link", name="Участки", exact=True).count(), 0)
                bottom_nav.get_by_role("link", name="Найти", exact=True).click()
            else:
                page.locator(".ws-sidebar").get_by_role("link", name="Найти", exact=True).click()

            page.wait_for_url("**/work/search/**")
            page.locator("#workspace-search").fill("Садовая 101")
            page.get_by_role("button", name="Найти", exact=True).click()
            page.get_by_text("Садовая 101", exact=True).first.click()
            page.wait_for_url(lambda url: urlsplit(url).path == f"/work/accounts/{self.account.pk}/")
            self.assertEqual(parse_qs(urlsplit(page.url).query).get("q"), ["Садовая 101"])
            page.wait_for_load_state("networkidle")

            self.assertTrue(page.get_by_role("heading", name="Садовая 101", exact=True).is_visible())
            self.assertTrue(page.get_by_text("E2E-METER-101", exact=True).is_visible())
            self.assertEqual(page.get_by_text("Секретный E2E Контакт", exact=True).count(), 0)
            self.assertEqual(page.get_by_text("+79990000999", exact=True).count(), 0)
            self.assertTrue(page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            ))
            self._assert_no_blocking_accessibility(page, f"{label} account")

            if viewport["width"] <= 640:
                page.locator(".ws-bottom-nav").get_by_role("link", name="Ещё", exact=True).click()
                page.wait_for_url("**/work/more/")
                page.wait_for_load_state("networkidle")
                self.assertTrue(page.get_by_role("heading", name="Ещё", exact=True).is_visible())
                self.assertEqual(
                    page.locator('.ws-bottom-nav a[aria-current="page"]').get_attribute("href"),
                    "/work/more/",
                )
                self.assertTrue(page.evaluate(
                    "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
                ))
                self._assert_no_blocking_accessibility(page, f"{label} more")

            page.goto(f"{self.live_server_url}/work/water/", wait_until="networkidle")
            navigation = page.locator(".ws-bottom-nav" if viewport["width"] <= 640 else ".ws-nav")
            self.assertEqual(
                navigation.locator('a[aria-current="page"]').get_attribute("href"),
                "/work/more/",
            )
            self.assertTrue(page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            ))
            self._assert_no_blocking_accessibility(page, f"{label} water navigation")
            artifacts = Path(settings.BASE_DIR) / "test-artifacts"
            artifacts.mkdir(exist_ok=True)
            page.screenshot(path=str(artifacts / f"night-staff-water-{label.replace(' ', '-')}.png"), full_page=True)

            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])
        finally:
            context.close()

    def test_workspace_critical_flow_chromium_desktop_and_mobile(self):
        session_cookie = self._verified_session_cookie()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                self._exercise(
                    browser, {"width": 1280, "height": 900}, "chromium desktop", session_cookie,
                )
                self._exercise(
                    browser, {"width": 390, "height": 844}, "chromium mobile", session_cookie,
                )
            finally:
                browser.close()

    def test_workspace_critical_flow_webkit_mobile(self):
        session_cookie = self._verified_session_cookie()
        with sync_playwright() as playwright:
            browser = playwright.webkit.launch(headless=True)
            try:
                self._exercise(
                    browser, {"width": 390, "height": 844}, "webkit mobile", session_cookie,
                )
            finally:
                browser.close()
