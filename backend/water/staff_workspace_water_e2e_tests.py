from datetime import timedelta
from io import StringIO

from axe_playwright_python.sync_playwright import Axe
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.test import Client
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from django.utils import timezone
from playwright.sync_api import sync_playwright

from .controller_scope import ControllerLineAccess
from .models import Account, Membership, Meter, SupplyNode, User, WaterGroup


class StaffWorkspaceWaterBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.manager = User.objects.create_user(username="water-hub-e2e", is_staff=True)
        self.manager.groups.add(Group.objects.get(name="Администратор ТСН"))
        account = Account.objects.create(number="WATER-E2E-1", plot="Тестовый участок воды")
        node = SupplyNode.objects.create(name="Тестовый узел воды")
        Meter.objects.create(serial="WATER-E2E-METER", kind="individual", node=node, account=account)

        self.line_user = User.objects.create_user(username="water-line-e2e", is_staff=True)
        self.line_user.groups.add(Group.objects.get(name="Контролёр воды"))
        self.line_group = WaterGroup.objects.create(name="Тестовая линия пользователя", node=node, source="meter")
        Membership.objects.create(
            account=account,
            group=self.line_group,
            starts=timezone.localdate() - timedelta(days=30),
        )
        ControllerLineAccess.objects.create(
            user=self.line_user,
            group=self.line_group,
            starts=timezone.localdate() - timedelta(days=30),
        )
        Meter.objects.create(serial="WATER-E2E-LINE", kind="line", node=node, group=self.line_group)

    def _verified_session_cookie(self, user, name):
        device = TOTPDevice.objects.create(user=user, name=name)
        client = Client()
        client.force_login(user)
        session = client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        return client.cookies[settings.SESSION_COOKIE_NAME].value

    def _assert_no_blocking_accessibility(self, page, label):
        results = Axe().run(page).response
        blocking = [
            violation for violation in results.get("violations", [])
            if violation.get("impact") in {"serious", "critical"}
            and any(str(tag).startswith("wcag") for tag in violation.get("tags") or [])
        ]
        self.assertEqual(blocking, [], f"{label}: {blocking}")

    def _exercise_manager(self, browser, label, session_cookie):
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
            response = page.goto(f"{self.live_server_url}/work/water/", wait_until="networkidle")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            self.assertTrue(page.get_by_role("heading", name="Вода", exact=True).is_visible())
            self.assertTrue(page.get_by_text("Активные счётчики", exact=True).is_visible())
            action = page.get_by_role("link").filter(has_text="Внести показания").first
            self.assertTrue(action.is_visible())
            self.assertTrue(page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            ))
            self._assert_no_blocking_accessibility(page, label)
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])
        finally:
            context.close()

    def _exercise_line_user(self, browser, label, session_cookie):
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
            response = page.goto(f"{self.live_server_url}/work/water/", wait_until="networkidle")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            self.assertTrue(page.get_by_role("heading", name="Показания линии", exact=True).is_visible())
            self.assertTrue(page.get_by_text("Тестовая линия пользователя", exact=True).is_visible())
            self.assertEqual(page.get_by_role("heading", name="Вода", exact=True).count(), 0)
            reading = page.get_by_label("Новое показание, м³").first
            self.assertTrue(reading.is_visible())
            self.assertEqual(reading.input_value(), "")
            self.assertEqual(reading.get_attribute("placeholder"), "Введите значение")
            self.assertTrue(page.get_by_text("Нужна другая дата?", exact=True).is_visible())
            self.assertTrue(page.get_by_role("button", name="Сохранить и отправить на проверку").is_visible())
            self.assertTrue(page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            ))
            self._assert_no_blocking_accessibility(page, label)
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])
        finally:
            context.close()

    def test_water_hub_mobile_chromium_and_webkit(self):
        manager_cookie = self._verified_session_cookie(self.manager, "water hub e2e device")
        line_cookie = self._verified_session_cookie(self.line_user, "water line e2e device")
        with sync_playwright() as playwright:
            chromium = playwright.chromium.launch(headless=True)
            try:
                self._exercise_manager(chromium, "water hub chromium mobile", manager_cookie)
            finally:
                chromium.close()
            webkit = playwright.webkit.launch(headless=True)
            try:
                self._exercise_manager(webkit, "water hub webkit mobile", manager_cookie)
            finally:
                webkit.close()

            chromium = playwright.chromium.launch(headless=True)
            try:
                self._exercise_line_user(chromium, "water line chromium mobile", line_cookie)
            finally:
                chromium.close()
            webkit = playwright.webkit.launch(headless=True)
            try:
                self._exercise_line_user(webkit, "water line webkit mobile", line_cookie)
            finally:
                webkit.close()
