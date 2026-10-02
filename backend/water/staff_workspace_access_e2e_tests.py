from io import StringIO

from axe_playwright_python.sync_playwright import Axe
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice
from playwright.sync_api import expect, sync_playwright

from .access_requests import ResidentAccessRequest
from .models import Account, LandPlot, Meter, Person, ResidentAccess, ResidentInvite, SupplyNode, User


class StaffWorkspaceAccessBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.staff = User.objects.create_user(username="access-e2e", is_staff=True)
        self.staff.groups.add(
            Group.objects.get(name="Администратор ТСН"),
            Group.objects.get(name="Закрытый реестр членов ТСН"),
        )
        self.account = Account.objects.create(number="ACCESS-E2E", plot="Тестовый участок Access E2E")
        self.chromium_person = Person.objects.create(full_name="Житель Chromium", email="grant-chromium@example.test")
        self.webkit_person = Person.objects.create(full_name="Житель WebKit", email="grant-webkit@example.test")
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

    def _exercise(self, browser, label, session_cookie, request_id, person_id, email):
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
            self.assertTrue(page.get_by_role("heading", name="Доступы и полномочия", exact=True).is_visible())
            self.assertEqual(page.locator(".ws-bottom-nav a").count(), 3)

            page.get_by_role("link", name="Выдать доступ жителю", exact=True).click()
            page.wait_for_load_state("networkidle")
            self.assertTrue(page.get_by_role("heading", name="Выдать доступ жителю", exact=True).is_visible())
            page.locator("#id_person").select_option(str(person_id))
            page.locator("#id_account").select_option(str(self.account.pk))
            page.locator("#id_email").fill(email)
            page.locator("#id_basis").fill(f"E2E проверка прав {label}")
            page.locator("#id_can_submit_water").check()
            page.get_by_role("button", name="Создать одноразовое приглашение", exact=True).click()
            page.wait_for_load_state("networkidle")
            self.assertTrue(page.locator("#invite-url").input_value())
            self._assert_no_blocking_accessibility(page, f"{label} granular invite")

            page.goto(f"{self.live_server_url}/work/access/requests/{request_id}/", wait_until="networkidle")
            self.assertTrue(page.get_by_text("+7 900 555-44-33", exact=True).is_visible())
            page.locator("#id_approve-account").select_option(str(self.account.pk))
            page.locator("#id_approve-role").select_option("owner")
            page.locator("#id_approve-decision_note").fill(f"E2E проверка {label}")
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
                self._exercise(
                    chromium, "Chromium mobile", session_cookie, chromium_id,
                    self.chromium_person.pk, self.chromium_person.email,
                )
            finally:
                chromium.close()
            webkit = playwright.webkit.launch(headless=True)
            try:
                self._exercise(
                    webkit, "WebKit mobile", session_cookie, webkit_id,
                    self.webkit_person.pk, self.webkit_person.email,
                )
            finally:
                webkit.close()

        self.chromium_request.refresh_from_db()
        self.webkit_request.refresh_from_db()
        self.assertEqual(self.chromium_request.status, ResidentAccessRequest.STATUS_APPROVED)
        self.assertEqual(self.webkit_request.status, ResidentAccessRequest.STATUS_APPROVED)
        self.assertEqual(ResidentInvite.objects.filter(access_request__in=[self.chromium_request, self.webkit_request]).count(), 2)
        self.assertEqual(ResidentInvite.objects.filter(person__isnull=False).count(), 2)
        self.assertEqual(ResidentAccess.objects.count(), 0)



class WorkbenchBrowserTests(StaticLiveServerTestCase):
    def test_panel_desktop_mobile_chromium_webkit(self):
        from pathlib import Path
        from django.utils import timezone
        from .models import LandPlot, Meter, PlotRelation, SupplyNode
        from .portal_permissions import PortalGrant
        from .resident_models import ResidentIdentity

        staff = User.objects.create_user(username='panel-browser-admin', is_staff=True, is_superuser=True)
        person = Person.objects.create(full_name='Тестовый житель панели')
        resident = User.objects.create_user(username='panel-browser-resident')
        ResidentIdentity.objects.create(user=resident, person=person, verified_by=staff, basis='E2E')
        account = Account.objects.create(number='BROWSER-PANEL', plot='Тестовый адрес 2')
        plot = LandPlot.objects.create(label='Тестовый участок панели', account=account)
        PlotRelation.objects.create(person=person, plot=plot, role='owner', starts=timezone.localdate(), document='E2E')
        PortalGrant.objects.create(person=person, account=account, starts=timezone.localdate(), basis='E2E', verified_by=staff)
        node = SupplyNode.objects.create(name='Тестовый узел панели')
        Meter.objects.create(serial='BROWSER-METER', kind='individual', node=node, account=account)
        device = TOTPDevice.objects.create(user=staff, name='panel e2e')
        self.client.force_login(staff)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        artifacts = Path(settings.BASE_DIR) / 'test-artifacts'
        artifacts.mkdir(exist_ok=True)
        with sync_playwright() as pw:
            for engine in ('chromium', 'webkit'):
                browser = getattr(pw, engine).launch(headless=True)
                try:
                    for width in (1440, 390):
                        context = browser.new_context(viewport={'width': width, 'height': 900})
                        context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': cookie, 'url': self.live_server_url}])
                        context.tracing.start(screenshots=True, snapshots=True)
                        page = context.new_page()
                        errors = []
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        try:
                            page.goto(self.live_server_url + '/work/more/')
                            page.get_by_role('link', name='Новая панель · только просмотр Жители и участки', exact=False).click()
                            page.wait_for_load_state('networkidle')
                            self.assertTrue(page.get_by_role('heading', name='Жители и участки', exact=True).is_visible())
                            page.get_by_label('Адрес, имя, логин или ID').fill('Тестовый адрес 2')
                            page.get_by_role('button', name='Найти', exact=True).click()
                            page.wait_for_load_state('networkidle')
                            page.locator('.wb-results a').first.click()
                            page.wait_for_load_state('networkidle')
                            self.assertTrue(page.get_by_text('BROWSER-METER', exact=True).is_visible())
                            page.locator('.wb-detail a[href^="?kind=person&id=%s&"]' % person.pk).first.click()
                            page.wait_for_load_state('networkidle')
                            self.assertTrue(page.get_by_role('heading', name=person.full_name, exact=True).is_visible())
                            self.assertEqual(page.get_by_label('Адрес, имя, логин или ID').input_value(), 'Тестовый адрес 2')
                            self.assertTrue(page.get_by_text('BROWSER-METER', exact=True).is_visible())
                            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'))
                            blocking = [v for v in Axe().run(page).response.get('violations', []) if v.get('impact') in {'serious', 'critical'} and any(t.startswith('wcag') for t in v.get('tags', []))]
                            self.assertEqual(blocking, [])
                            self.assertEqual(errors, [])
                            page.screenshot(path=str(artifacts / f'panel-{engine}-{width}.png'), full_page=True)
                        finally:
                            context.tracing.stop(path=str(artifacts / f'panel-{engine}-{width}.zip'))
                            context.close()
                finally:
                    browser.close()


class StaffRegistryEditorBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.staff = User.objects.create_user(
            username="registry-editor-e2e", is_staff=True, is_superuser=True,
        )
        self.person = Person.objects.create(
            full_name="Житель Редактор E2E", phone="+70000000001", email="editor-old@example.test",
        )
        self.account = Account.objects.create(
            number="EDITOR-E2E", plot="Старый адрес E2E", contact_name="Житель Редактор E2E",
            phone="+70000000002",
        )
        self.plot = LandPlot.objects.create(
            label="Участок редактора E2E", address="Старый ориентир E2E", account=self.account,
        )

    def _verified_session_cookie(self):
        device = TOTPDevice.objects.create(user=self.staff, name="registry editor e2e device")
        self.client.force_login(self.staff)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        return self.client.cookies[settings.SESSION_COOKIE_NAME].value

    def test_person_phone_and_plot_address_edit_mobile_chromium_webkit(self):
        cookie = self._verified_session_cookie()
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                browser = getattr(playwright, engine).launch(headless=True)
                context = browser.new_context(viewport={"width": 390, "height": 844})
                context.add_cookies([{
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": cookie,
                    "url": self.live_server_url,
                }])
                page = context.new_page()
                page_errors = []
                page.on("pageerror", lambda exc: page_errors.append(str(exc)))
                try:
                    page.goto(
                        f"{self.live_server_url}/work/panel/?q=%D0%96%D0%B8%D1%82%D0%B5%D0%BB%D1%8C%20%D0%A0%D0%B5%D0%B4%D0%B0%D0%BA%D1%82%D0%BE%D1%80%20E2E",
                        wait_until="networkidle",
                    )
                    person_result = page.locator(
                        f'.wb-results a[href*="kind=person"][href*="id={self.person.pk}"]'
                    )
                    expect(person_result).to_have_count(1)
                    person_result.click()
                    page.wait_for_load_state("networkidle")
                    page.get_by_role("link", name="Редактировать данные", exact=True).click()
                    page.locator("#id_phone").fill("+79990000011")
                    page.locator("#id_email").fill(f"editor-{engine}@example.test")
                    page.get_by_role("button", name="Сохранить изменения", exact=True).click()
                    page.wait_for_load_state("networkidle")
                    self.assertTrue(page.get_by_text("+79990000011", exact=False).is_visible())

                    page.goto(
                        f"{self.live_server_url}/work/panel/?kind=account&id={self.account.pk}",
                        wait_until="networkidle",
                    )
                    page.get_by_role("link", name="Редактировать карточку", exact=True).click()
                    page.locator("#id_plot").fill(f"Горная 2 · {engine}")
                    page.locator("#id_phone").fill("+79990000022")
                    page.get_by_role("button", name="Сохранить изменения", exact=True).click()
                    page.wait_for_load_state("networkidle")
                    self.assertTrue(page.get_by_role("heading", name=f"Горная 2 · {engine}", exact=True).is_visible())

                    page.goto(
                        f"{self.live_server_url}/work/panel/?kind=plot&id={self.plot.pk}",
                        wait_until="networkidle",
                    )
                    page.get_by_role("link", name="Изменить адрес", exact=True).click()
                    page.locator("#id_address").fill(f"Горная 2, ориентир {engine}")
                    page.get_by_role("button", name="Сохранить изменения", exact=True).click()
                    page.wait_for_load_state("networkidle")
                    self.assertTrue(page.get_by_text(f"Горная 2, ориентир {engine}", exact=False).is_visible())
                    self.assertTrue(page.evaluate(
                        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
                    ))
                    blocking = [
                        violation for violation in Axe().run(page).response.get("violations", [])
                        if violation.get("impact") in {"serious", "critical"}
                        and any(str(tag).startswith("wcag") for tag in violation.get("tags") or [])
                    ]
                    self.assertEqual(blocking, [])
                    self.assertEqual(page_errors, [])
                finally:
                    context.close()
                    browser.close()

        self.person.refresh_from_db()
        self.account.refresh_from_db()
        self.plot.refresh_from_db()
        self.assertEqual(self.person.phone, "+79990000011")
        self.assertEqual(self.account.phone, "+79990000022")
        self.assertTrue(self.account.plot.startswith("Горная 2"))
        self.assertTrue(self.plot.address.startswith("Горная 2, ориентир"))
        self.assertGreaterEqual(self.person.history.count(), 3)
        self.assertGreaterEqual(self.account.history.count(), 3)
        self.assertGreaterEqual(self.plot.history.count(), 3)


class WorkbenchMeterBindingBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.staff = User.objects.create_user(
            username="meter-workbench-e2e", is_staff=True, is_superuser=True,
        )
        self.account = Account.objects.create(number="METER-E2E", plot="Тестовый участок Meter E2E")
        self.node = SupplyNode.objects.create(name="Тестовый узел Meter E2E")

    def _cookie(self):
        device = TOTPDevice.objects.create(user=self.staff, name="meter workbench e2e")
        self.client.force_login(self.staff)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
        return self.client.cookies[settings.SESSION_COOKIE_NAME].value

    def test_bind_meter_from_workbench_mobile_chromium_webkit(self):
        cookie = self._cookie()
        with sync_playwright() as playwright:
            for engine in ("chromium", "webkit"):
                browser = getattr(playwright, engine).launch(headless=True)
                context = browser.new_context(viewport={"width": 390, "height": 844})
                context.add_cookies([{
                    "name": settings.SESSION_COOKIE_NAME, "value": cookie, "url": self.live_server_url,
                }])
                page = context.new_page()
                try:
                    page.goto(
                        f"{self.live_server_url}/work/panel/?kind=account&id={self.account.pk}",
                        wait_until="networkidle",
                    )
                    page.get_by_role("link", name="Привязать счётчик", exact=True).click()
                    page.locator("#id_node").select_option(str(self.node.pk))
                    serial = f"E2E-{engine}"
                    page.locator("#id_serial").fill(serial)
                    page.get_by_role("button", name="Создать и привязать", exact=True).click()
                    page.wait_for_load_state("networkidle")
                    expect(page.get_by_text(serial, exact=False)).to_be_visible()
                    self.assertTrue(page.evaluate(
                        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
                    ))
                    blocking = [
                        v for v in Axe().run(page).response.get("violations", [])
                        if v.get("impact") in {"serious", "critical"}
                        and any(str(tag).startswith("wcag") for tag in v.get("tags") or [])
                    ]
                    self.assertEqual(blocking, [])
                finally:
                    context.close()
                    browser.close()
        self.assertEqual(Meter.objects.filter(account=self.account, kind="individual").count(), 2)
