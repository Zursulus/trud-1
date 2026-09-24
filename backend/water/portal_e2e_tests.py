from contextlib import contextmanager
from datetime import date

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone
from playwright.sync_api import sync_playwright

from .models import Account, AppealCategory, Meter, ResidentAccess, SupplyNode, User


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
        self.category = AppealCategory.objects.create(name='Другое', active=True)

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
