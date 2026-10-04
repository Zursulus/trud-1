from contextlib import contextmanager
from datetime import date, timedelta

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone
from playwright.sync_api import sync_playwright

from .models import (
    Account, AppealCategory, ControllerReadingSubmission, Meter, ResidentAccess,
    ResidentAppeal, SupplyNode, User,
)


class ResidentPortalBrowserTests(StaticLiveServerTestCase):
    password = 'resident-browser-password-2026!'

    def setUp(self):
        self.account = Account.objects.create(number='MOBILE-1', plot='Садовая, 42')
        self.user = User.objects.create_user(
            username='mobile-resident@example.test', email='mobile-resident@example.test', password=self.password,
        )
        ResidentAccess.objects.create(user=self.user, account=self.account, role='owner', starts=date(2026, 1, 1))
        self.node = SupplyNode.objects.create(name='Мобильный узел')
        self.meter = Meter.objects.create(serial='MOBILE-METER', kind='individual', node=self.node, account=self.account)
        self.category, _ = AppealCategory.objects.get_or_create(name='Другое', defaults={'active': True})

    @contextmanager
    def browser_page(self, viewport):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport=viewport)
            page = context.new_page()
            page_errors = []
            console_errors = []
            page.on('pageerror', lambda exc: page_errors.append(str(exc)))
            page.on('console', lambda msg: console_errors.append(msg.text) if msg.type == 'error' and 'Failed to load resource' not in msg.text else None)
            try:
                yield page, page_errors, console_errors
            finally:
                context.close()
                browser.close()

    def _login(self, page):
        page.goto(f'{self.live_server_url}/admin/cabinet/login/')
        page.locator('#id_username').fill(self.user.username)
        page.locator('#id_password').fill(self.password)
        page.get_by_role('button', name='Войти', exact=True).click()
        page.wait_for_url('**/admin/cabinet/')

    def test_mobile_reference_design_contract_and_critical_flow(self):
        with self.browser_page({'width': 390, 'height': 844}) as (page, page_errors, console_errors):
            page.goto(f'{self.live_server_url}/admin/cabinet/login/')
            self.assertTrue(page.locator('.login-logo-row img').is_visible())
            self.assertEqual(page.locator('.site-header').count(), 0)
            self.assertTrue(page.evaluate("getComputedStyle(document.body).backgroundColor === 'rgb(255, 255, 255)'"))
            self._login(page)

            self.assertTrue(page.locator('.home-hero-image').is_visible())
            hero_height = page.locator('.home-hero-image').evaluate('(el) => el.getBoundingClientRect().height')
            self.assertGreaterEqual(hero_height, 180)
            self.assertLessEqual(hero_height, 195)
            self.assertTrue(page.locator('.home-plot-card').is_visible())
            self.assertEqual(page.locator('.home-shortcuts .shortcut').count(), 4)
            self.assertTrue(page.get_by_text('Что требует внимания', exact=True).is_visible())
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'))

            nav = page.locator('.bottom-nav')
            self.assertTrue(nav.is_visible())
            self.assertEqual(page.locator('.bottom-nav a').count(), 5)
            self.assertTrue(page.evaluate("getComputedStyle(document.querySelector('.bottom-nav')).borderRadius === '0px'"))
            self.assertTrue(page.evaluate("[...document.querySelectorAll('.bottom-nav a')].every(a => a.getBoundingClientRect().height >= 44)"))

            page.locator('.bottom-nav a[href$="/water/"]').click()
            page.wait_for_url('**/water/')
            self.assertTrue(page.locator('.subpage-header').is_visible())
            page.get_by_text('Передать показание', exact=True).first.click()
            page.locator('input[name="value"]').fill('12.345')
            page.locator('input[name="date"]').fill(timezone.localdate().isoformat())
            page.get_by_role('button', name='Передать показание', exact=True).click()
            page.wait_for_url('**/water/')
            self.assertTrue(page.get_by_text('На проверке', exact=True).is_visible())
            self.assertTrue(page.get_by_text('12,345 м³', exact=True).is_visible())
            self.assertTrue(page.get_by_role('status').is_visible())
            page.locator('.subpage-back').click()
            page.wait_for_url(f'**/account/{self.account.pk}/')

            page.locator('.shortcut-appeals').click()
            page.wait_for_url('**/appeals/')
            page.locator('.new-appeal-button').click()
            page.wait_for_url('**/appeal/new/')
            page.locator('#id_category').select_option(str(self.category.pk))
            page.locator('#id_subject').fill('Вопрос по участку')
            page.locator('#id_message').fill('Проверяю удобный сценарий обращения.')
            page.get_by_role('button', name='Отправить обращение', exact=True).click()
            page.wait_for_url('**/appeal/*/')
            self.assertTrue(page.get_by_text('Вопрос по участку', exact=True).is_visible())
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'))
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])

    def test_multiple_meters_are_distinguishable_and_retired_meter_has_no_submit_target(self):
        today = timezone.localdate()
        Meter.objects.create(
            serial='MOBILE-METER-2', kind='individual', node=self.node, account=self.account,
            commissioned_on=today - timedelta(days=10),
        )
        Meter.objects.create(
            serial='MOBILE-METER-RETIRED', kind='individual', node=self.node, account=self.account,
            commissioned_on=today - timedelta(days=20), retired_on=today - timedelta(days=1),
        )
        with self.browser_page({'width': 390, 'height': 844}) as (page, page_errors, console_errors):
            self._login(page)
            page.goto(
                f'{self.live_server_url}/admin/cabinet/account/{self.account.pk}/water/',
                wait_until='networkidle',
            )
            for serial in ('MOBILE-METER', 'MOBILE-METER-2', 'MOBILE-METER-RETIRED'):
                self.assertTrue(page.get_by_text(f'Счётчик {serial}', exact=True).is_visible())
            retired = page.locator('.water-summary').filter(has_text='MOBILE-METER-RETIRED')
            self.assertEqual(retired.locator('.reading-form').count(), 0)
            self.assertEqual(page.locator('.reading-form').count(), 2)
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])

    def test_invalid_water_form_can_be_corrected_repeated_and_cancelled_on_mobile(self):
        today = timezone.localdate()
        future = today + timedelta(days=1)
        water_url = f'{self.live_server_url}/admin/cabinet/account/{self.account.pk}/water/'
        with self.browser_page({'width': 390, 'height': 844}) as (page, page_errors, console_errors):
            self._login(page)
            page.goto(water_url)
            page.locator('.reading-details summary').click()
            page.locator('.reading-form input[name="value"]').fill('12.345')
            page.locator('.reading-form input[name="date"]').fill(future.isoformat())
            page.locator('.reading-form input[name="notes"]').fill('Сохранить примечание после ошибки')
            page.locator('.reading-form').evaluate('(form) => { form.noValidate = true; }')
            page.get_by_role('button', name='Передать показание', exact=True).click()
            page.wait_for_url('**/reading/')
            self.assertTrue(page.get_by_role('alert').is_visible())
            self.assertEqual(page.locator('#id_date').input_value(), future.isoformat())
            self.assertEqual(page.locator('#id_notes').input_value(), 'Сохранить примечание после ошибки')
            self.assertTrue(page.locator('.bottom-nav a.active[href$="/water/"]').is_visible())

            page.locator('#id_date').fill(today.isoformat())
            page.get_by_role('button', name='Передать показание', exact=True).click()
            page.wait_for_url('**/water/')
            self.assertTrue(page.get_by_role('status').is_visible())
            self.assertTrue(page.get_by_text('12,345 м³', exact=True).is_visible())

            page.locator('.reading-details summary').click()
            page.locator('.reading-form input[name="value"]').fill('13.456')
            page.get_by_role('button', name='Передать показание', exact=True).click()
            page.wait_for_url('**/water/')
            self.assertTrue(page.get_by_text('13,456 м³', exact=True).is_visible())
            self.assertEqual(page.locator('.water-summary .reading-list .reading-row').count(), 1)

            page.locator('.reading-details summary').click()
            page.locator('.reading-form input[name="value"]').fill('14.567')
            page.locator('.reading-form input[name="date"]').fill(future.isoformat())
            page.locator('.reading-form').evaluate('(form) => { form.noValidate = true; }')
            page.get_by_role('button', name='Передать показание', exact=True).click()
            page.wait_for_url('**/reading/')
            page.get_by_role('link', name='Отменить и вернуться к воде').click()
            page.wait_for_url('**/water/')
            self.assertTrue(page.get_by_text('13,456 м³', exact=True).is_visible())
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'))
            self.assertEqual(page.locator('.water-summary .reading-list .reading-row').count(), 1)
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])
        self.assertEqual(ControllerReadingSubmission.objects.filter(meter=self.meter).count(), 1)
        self.assertEqual(str(ControllerReadingSubmission.objects.get(meter=self.meter).value), '13.456')

    def test_desktop_reference_layout(self):
        with self.browser_page({'width': 1280, 'height': 900}) as (page, page_errors, console_errors):
            self._login(page)
            self.assertTrue(page.locator('.desktop-sidebar').is_visible())
            self.assertTrue(page.locator('.desktop-nav').is_visible())
            self.assertFalse(page.locator('.bottom-nav').is_visible())
            self.assertTrue(page.locator('.desktop-brand img').is_visible())
            self.assertTrue(page.locator('.desktop-nav a[href$="/payments/"]').is_visible())
            self.assertGreater(page.locator('.home-hero-image').evaluate('(el) => el.getBoundingClientRect().height'), 200)
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])

    def test_all_portal_sections_render_without_browser_errors(self):
        appeal = ResidentAppeal.objects.create(
            account=self.account,
            author=self.user,
            category=self.category,
            subject='Проверка маршрута',
            message='Тестовый диалог для browser-smoke.',
        )
        with self.browser_page({'width': 390, 'height': 844}) as (page, page_errors, console_errors):
            self._login(page)
            account_base = f'/admin/cabinet/account/{self.account.pk}'
            routes = [
                '/admin/cabinet/plots/',
                f'{account_base}/',
                f'{account_base}/payments/',
                f'{account_base}/water/',
                f'{account_base}/appeals/',
                f'{account_base}/appeal/new/',
                f'{account_base}/appeal/{appeal.pk}/',
                f'{account_base}/documents/',
                f'{account_base}/notifications/',
                f'{account_base}/more/',
                f'{account_base}/profile/',
                f'{account_base}/security/',
                '/admin/cabinet/password/',
            ]
            for route in routes:
                response = page.goto(f'{self.live_server_url}{route}', wait_until='networkidle')
                self.assertIsNotNone(response, route)
                self.assertEqual(response.status, 200, route)
                self.assertTrue(page.locator('body').is_visible(), route)
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'), route)
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])
