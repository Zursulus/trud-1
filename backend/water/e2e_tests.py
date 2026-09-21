"""Browser-level regression checks for critical staff/controller UI.

Run explicitly with:
    python backend/manage.py test water.e2e_tests -v 2

These tests complement, rather than duplicate, the faster Django-client tests in
water.tests. They exercise real browser navigation and JavaScript only against
test data and the Django test database.
"""
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from playwright.sync_api import sync_playwright

from .models import Account, ControllerReadingSubmission, Meter, Reading, SupplyNode, User


ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "test-artifacts"


class ControllerBrowserRegressionTests(StaticLiveServerTestCase):
    """Protect the controller Add -> capture flow and moderation UI."""

    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.controller = User.objects.create_user(username="controller-e2e", is_staff=True)
        self.controller.groups.add(Group.objects.get(name="Контролёр воды"))
        self.manager = User.objects.create_user(username="manager-e2e", is_staff=True)
        self.manager.groups.add(Group.objects.get(name="Администратор ТСН"))
        self.account = Account.objects.create(number="77", plot="Лесная 7")
        self.node = SupplyNode.objects.create(name="Узел контролёра E2E")
        self.meter = Meter.objects.create(
            serial="CTRL-E2E-1", kind="individual", node=self.node, account=self.account,
        )

    def _verified_session_cookie(self, user=None):
        user = user or self.controller
        device, _ = TOTPDevice.objects.get_or_create(
            user=user, defaults={"name": "e2e test device"},
        )
        self.client.force_login(user)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        return self.client.cookies[settings.SESSION_COOKIE_NAME].value

    @contextmanager
    def _browser_page(self, viewport, user=None):
        # Django ORM/test-client work must happen before Playwright starts its
        # sync facade, which owns an event loop in this thread.
        session_cookie = self._verified_session_cookie(user)
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport=viewport)
            context.add_cookies([{
                "name": settings.SESSION_COOKIE_NAME,
                "value": session_cookie,
                "url": self.live_server_url,
            }])
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            page_errors = []
            console_errors = []
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))
            page.on(
                "console",
                lambda msg: console_errors.append(msg.text)
                if msg.type == "error" and "Failed to load resource" not in msg.text
                else None,
            )
            try:
                yield page, page_errors, console_errors
            except Exception:
                context.tracing.stop(path=str(ARTIFACT_DIR / f"{self._testMethodName}.zip"))
                raise
            else:
                context.tracing.stop()
            finally:
                context.close()
                browser.close()

    def test_admin_add_link_opens_capture_and_runs_identity_javascript(self):
        with self._browser_page({"width": 1280, "height": 900}) as (page, page_errors, console_errors):
            response = page.goto(f"{self.live_server_url}/admin/")
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)

            add_link = page.locator(
                'a[href="/admin/water/controllerreadingsubmission/capture/"]'
            ).first
            self.assertTrue(add_link.is_visible())
            add_link.click()
            page.wait_for_url("**/admin/water/controllerreadingsubmission/capture/")

            self.assertTrue(page.get_by_role("button", name="Отправить на проверку").is_visible())
            select = page.locator("#id_meter")
            select.select_option(str(self.meter.pk))

            option_text = page.locator(
                f'#id_meter option[value="{self.meter.pk}"]'
            ).inner_text()
            self.assertEqual(option_text.strip(), "Лесная 7")
            self.assertTrue(page.locator("#identity").is_visible())
            self.assertEqual(page.locator("#address").inner_text().strip(), "Лесная 7")
            self.assertEqual(page.locator("#account-id").count(), 0)
            self.assertEqual(page.locator("#serial").count(), 0)
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])

    def test_capture_form_remains_usable_on_mobile_viewport(self):
        with self._browser_page({"width": 390, "height": 844}) as (page, page_errors, console_errors):
            response = page.goto(
                f"{self.live_server_url}/admin/water/controllerreadingsubmission/add/"
            )
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)

            for selector in ("#id_meter", "#id_value", "#id_date", "#id_notes"):
                self.assertTrue(page.locator(selector).is_visible())
            self.assertEqual(page.locator("#id_photo").count(), 0)
            for label in ("Позавчера", "Вчера", "Сегодня"):
                self.assertTrue(page.get_by_role("button", name=label, exact=True).is_visible())
            self.assertTrue(page.get_by_role("button", name="Отправить на проверку").is_visible())
            self.assertTrue(page.evaluate(
                "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            ))
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])

    def test_manager_moderates_with_comparison_and_clear_actions(self):
        today = timezone.localdate()
        Reading.objects.create(
            meter=self.meter, date=today - timedelta(days=30), value=Decimal("1100.000"),
        )
        submission = ControllerReadingSubmission.objects.create(
            meter=self.meter, date=today, value=Decimal("1183.000"),
            submitted_by=self.controller, notes="Контрольный обход",
        )

        with self._browser_page({"width": 1280, "height": 900}, self.manager) as (page, page_errors, console_errors):
            response = page.goto(
                f"{self.live_server_url}/admin/water/controllerreadingsubmission/{submission.pk}/change/"
            )
            self.assertIsNotNone(response)
            self.assertEqual(response.status, 200)
            self.assertTrue(page.get_by_text("Проверка показания", exact=True).is_visible())
            self.assertTrue(page.get_by_text("Предыдущее утверждённое", exact=True).is_visible())
            self.assertTrue(page.get_by_text("Разница", exact=True).is_visible())
            self.assertTrue(page.get_by_role("button", name="Принять показание", exact=True).is_visible())
            self.assertTrue(page.get_by_label("Комментарий при отклонении (необязательно)").is_visible())
            self.assertTrue(page.get_by_role("button", name="Отклонить", exact=True).is_visible())

            page.get_by_role("button", name="Принять показание", exact=True).click()
            confirmation = page.get_by_text("Показание принято и добавлено в журнал.", exact=True)
            confirmation.wait_for(state="visible")
            self.assertTrue(confirmation.is_visible())
            self.assertTrue(page.get_by_role("link", name="Открыть журнал показаний", exact=True).is_visible())
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])
