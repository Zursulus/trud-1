"""Synthetic browser regressions for resident recovery and daily staff editing.

Run explicitly with manage.py test water.ui_flow_polish_e2e_tests. Fixtures are
shared with the existing role-specific E2E suites; no production data is used.
"""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from axe_playwright_python.sync_playwright import Axe
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.db import connections
from django.utils import timezone
from playwright.sync_api import sync_playwright

from . import portal_e2e_tests as resident_fixtures
from . import staff_workspace_e2e_tests as staff_fixtures
from .access_control import AccessAssignment
from .models import BillingPeriod, Charge, ControllerReadingSubmission, Meter, Payment, Person, ResidentAccess
from .resident_models import ResidentIdentity


VARIANTS = (
    ("chromium", {"width": 390, "height": 844}),
    ("chromium", {"width": 1280, "height": 900}),
    ("webkit", {"width": 390, "height": 844}),
)


class BrowserChecks:
    def database(self, operation):
        # Playwright's sync API keeps an event loop on the calling thread.
        # Keep Django's async-safety guard enabled and use a separate DB thread.
        def invoke():
            try:
                return operation()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as worker:
            return worker.submit(invoke).result(timeout=10)

    @contextmanager
    def browser(self, engine, viewport, scenario, cookie=None, expected_forbidden_document=None):
        artifacts = Path(settings.BASE_DIR) / "test-artifacts" / "ui-flow-polish"
        artifacts.mkdir(parents=True, exist_ok=True)
        tag = f"{scenario}-{engine}-{viewport['width']}"
        with sync_playwright() as pw:
            browser = getattr(pw, engine).launch(headless=True)
            context = browser.new_context(viewport=viewport)
            # This existing root image is served separately by Nginx in production.
            image = Path(settings.BASE_DIR).parent / "ordzhonikidze-sunset.webp"
            context.route("**/ordzhonikidze-sunset.webp", lambda route: route.fulfill(
                path=str(image), content_type="image/webp"))
            context.route("**/favicon.ico", lambda route: route.fulfill(status=204))
            if cookie:
                context.add_cookies([{"name": settings.SESSION_COOKIE_NAME,
                                     "value": cookie, "url": self.live_server_url}])
            page = context.new_page()
            page.set_default_timeout(10000)
            errors, console = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: console.append((message.text, urlsplit(message.location.get("url", "")).path))
                    if message.type == "error" else None)
            context.tracing.start(screenshots=True, snapshots=True)
            try:
                yield page, tag
                self.assertEqual(errors, [], tag)
                expected_text = "Failed to load resource: the server responded with a status of 403 (Forbidden)"
                unexpected = [(text, path) for text, path in console
                              if not (expected_forbidden_document and path == expected_forbidden_document and text == expected_text)]
                self.assertEqual(unexpected, [], tag)
            except Exception:
                page.screenshot(path=str(artifacts / f"{tag}-FAILED.png"), full_page=True)
                context.tracing.stop(path=str(artifacts / f"{tag}-FAILED.zip"))
                raise
            else:
                context.tracing.stop()
            finally:
                context.close()
                browser.close()

    def check_page(self, page, tag):
        page.wait_for_load_state("networkidle")
        self.assertTrue(page.evaluate(
            "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"), tag)
        results = Axe().run(page).response
        blocking = [item for item in results.get("violations", [])
                    if item.get("impact") in {"serious", "critical"}
                    and any(str(rule).startswith("wcag") for rule in item.get("tags", []))]
        self.assertEqual(blocking, [], f"{tag}: {blocking}")
        page.screenshot(path=str(Path(settings.BASE_DIR) / "test-artifacts" / "ui-flow-polish" / f"{tag}.png"), full_page=True)

    def assert_url(self, page, path, **query):
        self.assertEqual(urlsplit(page.url).path, path)
        self.assertEqual(parse_qs(urlsplit(page.url).query), {key: [str(value)] for key, value in query.items()})


class ResidentFlowPolishBrowserTests(BrowserChecks, StaticLiveServerTestCase):
    password = resident_fixtures.ResidentPortalBrowserTests.password
    setUp = resident_fixtures.ResidentPortalBrowserTests.setUp
    _login = resident_fixtures.ResidentPortalBrowserTests._login

    def test_expired_submission_returns_to_water_without_post_replay(self):
        water = f"/admin/cabinet/account/{self.account.pk}/water/"
        reading = f"/admin/cabinet/account/{self.account.pk}/meter/{self.meter.pk}/reading/"
        for index, (engine, viewport) in enumerate(VARIANTS):
            with self.subTest(engine=engine, viewport=viewport), self.browser(engine, viewport, "resident-expired") as (page, tag):
                self._login(page)
                page.goto(self.live_server_url + water, wait_until="networkidle")
                page.locator(".reading-details summary").click()
                page.locator('.reading-form input[name="value"]').fill(str(20 + index))
                page.locator('.reading-form input[name="notes"]').fill("Synthetic interrupted draft")
                snapshot = lambda: list(ControllerReadingSubmission.objects.values_list("pk", "value", "notes"))
                before = self.database(snapshot)
                page.context.clear_cookies(name=settings.SESSION_COOKIE_NAME)
                page.get_by_role("button", name="Передать показание", exact=True).click()
                page.wait_for_url("**/admin/cabinet/login/**")
                self.assertEqual(self.database(snapshot), before)
                requests = []
                page.on("request", lambda request: requests.append((request.method, urlsplit(request.url).path)))
                page.locator("#id_username").fill(self.user.username)
                page.locator("#id_password").fill(self.password)
                page.get_by_role("button", name="Войти", exact=True).click()
                page.wait_for_url("**/water/")
                self.assert_url(page, water)
                self.assertNotIn(("POST", reading), requests)
                self.assertEqual(self.database(snapshot), before)
                self.assertEqual(page.locator('.reading-form input[name="value"]').input_value(), "")
                self.check_page(page, tag + "-returned")
                # A new deliberate submit, then same-day correction, creates one pending request.
                page.locator(".reading-details summary").click()
                page.locator('.reading-form input[name="value"]').fill(str(30 + index))
                page.get_by_role("button", name="Передать показание", exact=True).click()
                page.wait_for_url("**/water/")
                page.locator(".reading-details summary").click()
                page.locator('.reading-form input[name="value"]').fill(str(40 + index))
                page.get_by_role("button", name="Передать показание", exact=True).click()
                page.wait_for_url("**/water/")
                self.assertEqual(self.database(lambda: ControllerReadingSubmission.objects.filter(meter=self.meter).count()), 1)
                self.assertEqual(self.database(lambda: ControllerReadingSubmission.objects.get(meter=self.meter).value), Decimal(40 + index))
                self.assertTrue(page.get_by_role("status").is_visible())
                self.check_page(page, tag + "-corrected")

    def test_no_access_can_leave_using_csrf_protected_logout(self):
        access = ResidentAccess.objects.get(user=self.user, account=self.account)
        for engine, viewport in VARIANTS:
            with self.subTest(engine=engine, viewport=viewport), self.browser(
                    engine, viewport, "resident-no-access", expected_forbidden_document="/admin/cabinet/") as (page, tag):
                self._login(page)
                access.ends = timezone.localdate()
                self.database(access.save)
                response = page.goto(self.live_server_url + "/admin/cabinet/", wait_until="networkidle")
                self.assertEqual(response.status, 403)
                self.assertTrue(page.get_by_role("heading", name="Доступ к участку не найден").is_visible())
                form = page.locator('form[action="/admin/cabinet/logout/"]')
                self.assertEqual(form.get_attribute("method"), "post")
                self.assertTrue(form.locator('input[name="csrfmiddlewaretoken"]').input_value())
                self.check_page(page, tag)
                with page.expect_request(lambda request: urlsplit(request.url).path == "/admin/cabinet/logout/") as logout:
                    page.get_by_role("button", name="Выйти из кабинета", exact=True).click()
                self.assertEqual(logout.value.method, "POST")
                self.assertIn("csrfmiddlewaretoken=", logout.value.post_data)
                page.wait_for_url("**/admin/cabinet/login/")
                page.goto(f"{self.live_server_url}/admin/cabinet/account/{self.account.pk}/water/")
                page.wait_for_url("**/admin/cabinet/login/**")
                self.assertTrue(page.get_by_role("button", name="Войти", exact=True).is_visible())
                access.ends = None
                self.database(access.save)

    def test_finance_links_reach_real_empty_and_populated_sections(self):
        url = f"{self.live_server_url}/admin/cabinet/account/{self.account.pk}/payments/"
        for index, (engine, viewport) in enumerate(VARIANTS):
            with self.subTest(engine=engine, viewport=viewport), self.browser(engine, viewport, "resident-finance") as (page, tag):
                self._login(page)
                page.goto(url, wait_until="networkidle")
                if index == 0:
                    self.assertTrue(page.locator("#charges").get_by_text("Утверждённых начислений пока нет.", exact=True).is_visible())
                    self.assertTrue(page.locator("#payments").get_by_text("Подтверждённых оплат пока нет.", exact=True).is_visible())
                links = page.get_by_role("navigation", name="Разделы финансовой карточки")
                for label, fragment in (("Начисления", "charges"), ("Оплаты", "payments")):
                    link = links.get_by_role("link", name=label, exact=True)
                    self.assertEqual(link.get_attribute("href"), "#" + fragment)
                    self.assertGreaterEqual(link.bounding_box()["height"], 44)
                    link.click()
                    self.assertEqual(urlsplit(page.url).fragment, fragment)
                    self.assertTrue(page.locator("#" + fragment).get_by_role("heading", name=label).is_visible())
                page.go_back()
                self.assertEqual(urlsplit(page.url).fragment, "charges")
                self.assertEqual(page.get_by_role("link", name="Скачать справку об оплатах", exact=True).count(), 0)
                self.check_page(page, tag + "-sections")
                if index == 0:
                    today = timezone.localdate()
                    def seed_finance():
                        period = BillingPeriod.objects.create(starts=today-timedelta(days=30), ends=today+timedelta(days=1))
                        Charge.objects.create(account=self.account, period=period, kind="service", amount=Decimal("125"), status="approved")
                        Payment.objects.create(account=self.account, paid_on=today, amount=Decimal("50"), method="bank", reference="SYNTHETIC-RECEIPT", status="confirmed")
                    self.database(seed_finance)
                    page.reload(wait_until="networkidle")
                self.assertTrue(page.locator("#payments").get_by_text("SYNTHETIC-RECEIPT", exact=False).is_visible())
                self.assertGreater(page.locator("#charges .ledger-row").count(), 0)
                page.get_by_role("link", name="Вопрос по оплате", exact=True).click()
                page.wait_for_url("**/appeal/new/")
                self.assertTrue(page.locator("#id_subject").is_visible())
                page.go_back(wait_until="networkidle")
                page.get_by_role("link", name="Документы участка", exact=True).click()
                page.wait_for_url("**/documents/")
                self.check_page(page, tag + "-documents")

    def test_password_change_receipt_is_shown_once_and_session_survives(self):
        for engine, viewport in VARIANTS:
            with self.subTest(engine=engine, viewport=viewport), self.browser(engine, viewport, "resident-password") as (page, tag):
                self._login(page)
                page.goto(self.live_server_url + "/admin/cabinet/password/")
                new_password = f"Synthetic-new-{engine}-{viewport['width']}-2026!"
                page.locator("#id_old_password").fill(self.password)
                page.locator("#id_new_password1").fill(new_password)
                page.locator("#id_new_password2").fill(new_password)
                page.get_by_role("button", name="Сохранить новый пароль", exact=True).click()
                page.wait_for_url("**/admin/cabinet/")
                receipt = page.get_by_role("status").filter(has_text="Пароль изменён.")
                self.assertEqual(receipt.count(), 1)
                self.assertTrue(receipt.is_visible())
                def password_saved():
                    self.user.refresh_from_db()
                    return self.user.check_password(new_password)
                self.assertTrue(self.database(password_saved))
                self.password = new_password
                self.check_page(page, tag + "-receipt")
                page.reload(wait_until="networkidle")
                self.assertEqual(page.get_by_role("status").filter(has_text="Пароль изменён.").count(), 0)
                self.assertTrue(page.get_by_role("heading", name="Ваш участок", exact=True).is_visible())


class StaffFlowPolishBrowserTests(BrowserChecks, StaticLiveServerTestCase):
    _verified_session_cookie = staff_fixtures.StaffWorkspaceBrowserTests._verified_session_cookie

    def setUp(self):
        staff_fixtures.StaffWorkspaceBrowserTests.setUp(self)
        person = Person.objects.create(full_name="Synthetic scoped meter operator")
        ResidentIdentity.objects.create(user=self.manager, person=person, verified_by=self.manager, basis="Synthetic E2E identity")
        AccessAssignment.objects.create(person=person, role_code="e2e-topology", role_label="Synthetic topology only",
            allowed_capabilities=["water.topology.manage"], capabilities=["water.topology.manage"],
            scope_type="supply_node", scope_object_id=Meter.objects.get(account=self.account).node_id,
            starts=timezone.localdate()-timedelta(days=1), basis="Synthetic scoped browser fixture", granted_by=self.manager)

    def test_find_edit_and_bind_keep_account_context_and_private_contacts(self):
        cookie = self._verified_session_cookie()
        q = "Садовая 101"
        for engine, viewport in VARIANTS:
            with self.subTest(engine=engine, viewport=viewport), self.browser(engine, viewport, "staff-daily", cookie) as (page, tag):
                page.goto(self.live_server_url + "/work/")
                nav = page.locator(".ws-bottom-nav" if viewport["width"] < 640 else ".ws-sidebar")
                nav.get_by_role("link", name="Найти", exact=True).click()
                page.wait_for_url("**/work/search/**")
                page.locator("#workspace-search").fill(q)
                page.get_by_role("button", name="Найти", exact=True).click()
                page.locator(".ws-result").first.click()
                page.wait_for_url("**/work/accounts/*/**")
                path = f"/work/accounts/{self.account.pk}/"
                self.assert_url(page, path, q=q)
                self.check_page(page, tag + "-account")
                page.get_by_role("link", name="Редактировать карточку", exact=True).click()
                page.wait_for_url("**/edit/**")
                self.assertEqual(page.locator("#id_contact_name, #id_phone").count(), 0)
                self.assertEqual(page.get_by_text("Секретный E2E Контакт", exact=True).count(), 0)
                self.assertEqual(page.get_by_text("+79990000999", exact=True).count(), 0)
                self.check_page(page, tag + "-edit")
                page.locator("#id_plot").fill(q + " · исправлено")
                page.locator("#id_notes").fill("Synthetic daily edit")
                page.get_by_role("button", name="Сохранить изменения", exact=True).click()
                page.wait_for_url("**/work/accounts/*/**")
                self.assert_url(page, path, q=q)
                self.database(self.account.refresh_from_db)
                self.assertEqual(self.account.plot, q + " · исправлено")
                self.assertEqual(self.account.contact_name, "Секретный E2E Контакт")
                self.assertEqual(self.account.phone, "+79990000999")
                version = self.account.version
                page.get_by_role("link", name="Редактировать карточку", exact=True).click()
                page.locator("#id_notes").fill("Unsubmitted draft")
                page.get_by_role("link", name="Отмена", exact=True).click()
                self.assert_url(page, path, q=q)
                self.database(self.account.refresh_from_db)
                self.assertEqual(self.account.version, version)
                page.get_by_role("link", name="Привязать счётчик", exact=True).click()
                page.wait_for_url("**/meters/bind/**")
                node = self.database(lambda: Meter.objects.get(serial="E2E-METER-101").node_id)
                page.locator("#id_node").select_option(str(node))
                page.locator("#id_serial").fill("E2E-METER-101")
                page.get_by_role("button", name="Создать и привязать", exact=True).click()
                self.assertTrue(page.get_by_text("Счётчик с таким номером уже существует на выбранном узле.", exact=True).is_visible())
                serial = f"FLOW-{engine}-{viewport['width']}"
                page.locator("#id_serial").fill(serial)
                page.get_by_role("button", name="Создать и привязать", exact=True).click()
                page.wait_for_url("**/work/accounts/*/**")
                self.assert_url(page, path, q=q)
                self.assertEqual(self.database(lambda: Meter.objects.get(serial=serial).account_id), self.account.pk)
                self.check_page(page, tag + "-bound")
                page.get_by_role("link", name="Привязать счётчик", exact=True).click()
                page.locator("#id_serial").fill(serial + "-CANCELLED")
                page.get_by_role("link", name="Отмена", exact=True).click()
                self.assert_url(page, path, q=q)
                self.assertFalse(self.database(lambda: Meter.objects.filter(serial=serial + "-CANCELLED").exists()))
                page.get_by_role("link", name="К результатам поиска", exact=True).click()
                self.assert_url(page, "/work/search/", q=q)
                self.assertEqual(page.locator("#workspace-search").input_value(), q)

    def test_privileged_panel_binding_cancel_returns_to_its_own_panel(self):
        self.manager.is_superuser = True
        self.manager.save(update_fields=["is_superuser"])
        cookie = self._verified_session_cookie()
        for engine, viewport in VARIANTS:
            with self.subTest(engine=engine, viewport=viewport), self.browser(engine, viewport, "staff-panel", cookie) as (page, tag):
                page.goto(f"{self.live_server_url}/work/panel/?kind=account&id={self.account.pk}")
                page.get_by_role("link", name="Привязать счётчик", exact=True).click()
                page.wait_for_url("**/meters/bind/**")
                self.check_page(page, tag + "-bind")
                page.locator("#id_serial").fill("PANEL-UNSUBMITTED")
                page.get_by_role("link", name="Отмена", exact=True).click()
                self.assert_url(page, "/work/panel/", kind="account", id=self.account.pk)
                self.assertFalse(self.database(lambda: Meter.objects.filter(serial="PANEL-UNSUBMITTED").exists()))
