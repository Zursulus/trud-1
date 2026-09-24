"""Focused automated accessibility gate for public and resident surfaces.

This intentionally fails CI only on serious/critical axe violations that carry
WCAG tags. Minor/moderate findings remain visible to manual audits without making
routine CI noisy.
"""
from datetime import date
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from axe_playwright_python.sync_playwright import Axe
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import SimpleTestCase
from playwright.sync_api import sync_playwright

from .models import Account, Meter, ResidentAccess, SupplyNode, User


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BLOCKING_IMPACTS = {"serious", "critical"}


def _blocking_wcag_violations(page):
    results = Axe().run(page)
    violations = []
    for violation in results.response.get("violations", []):
        tags = violation.get("tags") or []
        if (
            violation.get("impact") in BLOCKING_IMPACTS
            and any(str(tag).startswith("wcag") for tag in tags)
        ):
            violations.append(violation)
    return violations


def _format_violations(label, violations):
    lines = [f"{label}: {len(violations)} blocking accessibility violation(s)"]
    for violation in violations:
        targets = []
        for node in violation.get("nodes", [])[:4]:
            target = node.get("target") or []
            targets.append(" > ".join(str(part) for part in target))
        target_text = ", ".join(filter(None, targets)) or "target unavailable"
        lines.append(
            f"- {violation.get('id')} [{violation.get('impact')}]: "
            f"{violation.get('help')} | {target_text}"
        )
    return "\n".join(lines)


def assert_no_blocking_wcag(testcase, page, label):
    violations = _blocking_wcag_violations(page)
    if violations:
        testcase.fail(_format_violations(label, violations))


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


class PublicAccessibilityTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        handler = partial(QuietStaticHandler, directory=str(REPOSITORY_ROOT))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.public_url = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.http_thread = Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.http_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.http_thread.join(timeout=5)
        super().tearDownClass()

    def test_public_home_has_no_blocking_wcag_violations(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for viewport, label in (
                    ({"width": 390, "height": 844}, "public mobile"),
                    ({"width": 1280, "height": 900}, "public desktop"),
                ):
                    context = browser.new_context(viewport=viewport)
                    try:
                        page = context.new_page()
                        response = page.goto(f"{self.public_url}/index.html", wait_until="networkidle")
                        self.assertIsNotNone(response)
                        self.assertEqual(response.status, 200)
                        assert_no_blocking_wcag(self, page, label)
                    finally:
                        context.close()
            finally:
                browser.close()


class ResidentPortalAccessibilityTests(StaticLiveServerTestCase):
    password = "resident-a11y-password-2026!"

    def setUp(self):
        self.account = Account.objects.create(number="A11Y-1", plot="Садовая, 42")
        self.user = User.objects.create_user(
            username="resident-a11y@example.test",
            email="resident-a11y@example.test",
            password=self.password,
        )
        ResidentAccess.objects.create(
            user=self.user,
            account=self.account,
            role="owner",
            starts=date(2026, 1, 1),
        )
        self.node = SupplyNode.objects.create(name="A11Y узел")
        Meter.objects.create(
            serial="A11Y-METER",
            kind="individual",
            node=self.node,
            account=self.account,
        )

    def _login(self, page):
        page.goto(f"{self.live_server_url}/admin/cabinet/login/", wait_until="networkidle")
        page.locator("#id_username").fill(self.user.username)
        page.locator("#id_password").fill(self.password)
        page.get_by_role("button", name="Войти", exact=True).click()
        page.wait_for_url("**/admin/cabinet/")
        page.wait_for_load_state("networkidle")

    def test_resident_login_dashboard_and_water_have_no_blocking_wcag_violations(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for viewport, label in (
                    ({"width": 390, "height": 844}, "resident mobile"),
                    ({"width": 1280, "height": 900}, "resident desktop"),
                ):
                    context = browser.new_context(viewport=viewport)
                    try:
                        page = context.new_page()
                        page.goto(
                            f"{self.live_server_url}/admin/cabinet/login/",
                            wait_until="networkidle",
                        )
                        assert_no_blocking_wcag(self, page, f"{label} login")

                        self._login(page)
                        assert_no_blocking_wcag(self, page, f"{label} dashboard")

                        page.goto(
                            f"{self.live_server_url}/admin/cabinet/account/{self.account.pk}/water/",
                            wait_until="networkidle",
                        )
                        assert_no_blocking_wcag(self, page, f"{label} water")
                    finally:
                        context.close()
            finally:
                browser.close()
