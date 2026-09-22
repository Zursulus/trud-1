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

    def test_mobile_login_home_water_appeal_and_navigation(self):
        with self.browser_page({'width': 390, 'height': 844}) as (page, page_errors, console_errors):
            self._login(page)
            self.assertTrue(page.get_by_text('Что требует внимания', exact=True).is_visible())
            self.assertTrue(page.get_by_role('link', name='Передать показание').first.is_visible())
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'))
            nav_links = page.locator('.bottom-nav a')
            self.assertEqual(nav_links.count(), 5)
            self.assertTrue(page.evaluate("[...document.querySelectorAll('.bottom-nav a')].every(a => a.getBoundingClientRect().height >= 44)"))

            page.locator('.bottom-nav a[href$="/water/"]').click()
            page.wait_for_url('**/water/')
            page.get_by_text('Передать показание', exact=True).first.click()
            page.locator('input[name="value"]').fill('12.345')
            page.locator('input[name="date"]').fill(timezone.localdate().isoformat())
            page.get_by_role('button', name='Передать показание', exact=True).click()
            page.wait_for_url(f'**/account/{self.account.pk}/')

            page.locator('a[href$="/appeal/new/"]').first.click()
            page.locator('#id_category').select_option(str(self.category.pk))
            page.locator('#id_subject').fill('Вопрос по участку')
            page.locator('#id_message').fill('Проверяю удобный сценарий обращения.')
            page.get_by_role('button', name='Отправить обращение', exact=True).click()
            page.wait_for_url('**/appeal/*/')
            self.assertEqual(page.get_by_text('Правление ТСН «Труд-1»', exact=True).count(), 0)
            self.assertTrue(page.get_by_text('Вопрос по участку', exact=True).is_visible())
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'))
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])

    def test_desktop_uses_side_navigation(self):
        with self.browser_page({'width': 1280, 'height': 900}) as (page, page_errors, console_errors):
            self._login(page)
            self.assertTrue(page.locator('.desktop-nav').is_visible())
            self.assertFalse(page.locator('.bottom-nav').is_visible())
            self.assertTrue(page.locator('.desktop-nav a[href$="/payments/"]').is_visible())
            self.assertEqual(page_errors, [])
            self.assertEqual(console_errors, [])
