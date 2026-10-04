"""Synthetic invitation recovery and responsive activation checks."""
from pathlib import Path

from axe_playwright_python.sync_playwright import Axe
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse
from playwright.sync_api import sync_playwright

from .models import Account, ResidentAccess, User
from .portal import issue_invite


class InvitationReadinessBrowserTests(StaticLiveServerTestCase):
    def setUp(self):
        self.account = Account.objects.create(number='NIGHT-INVITE', plot='Тестовый участок 42')
        self.recipient = User.objects.create_user(
            username='night-invite@example.test', email='night-invite@example.test',
            password='night-invite-password',
        )
        self.other = User.objects.create_user(username='night-other@example.test')
        self.invite, token = issue_invite(self.account, self.recipient.email, 'owner')
        self.invite_path = reverse('resident_invite', args=[token])
        self.artifacts = Path(settings.BASE_DIR) / 'test-artifacts'
        self.artifacts.mkdir(exist_ok=True)

    def _check_page(self, page, label):
        self.assertTrue(page.evaluate(
            'document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'
        ))
        violations = [v for v in Axe().run(page).response.get('violations', [])
                      if v.get('impact') in {'serious', 'critical'}
                      and any(t.startswith('wcag') for t in v.get('tags', []))]
        self.assertEqual(violations, [], label)
        page.screenshot(path=str(self.artifacts / f'night-{label}.png'), full_page=True)

    def test_wrong_cabinet_invite_recovery_mobile(self):
        self.client.force_login(self.other)
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={'width': 390, 'height': 844})
            context.add_cookies([{'name': settings.SESSION_COOKIE_NAME, 'value': cookie,
                                 'url': self.live_server_url}])
            try:
                page = context.new_page()
                response = page.goto(self.live_server_url + self.invite_path, wait_until='networkidle')
                self.assertEqual(response.status, 403)
                button = page.get_by_role('button', name='Выйти и продолжить')
                self.assertGreaterEqual(button.bounding_box()['height'], 44)
                self._check_page(page, 'invite-wrong-cabinet-mobile')
                button.click()
                page.get_by_role('link', name='Войти и продолжить').click()
                page.locator('#id_username').fill(self.recipient.username)
                page.locator('#id_password').fill('night-invite-password')
                page.get_by_role('button', name='Войти', exact=True).click()
                page.wait_for_url(self.live_server_url + self.invite_path)
                self._check_page(page, 'invite-confirm-mobile')
                page.get_by_role('button', name='Подключить лицевой счёт участка').click()
                page.wait_for_url('**/admin/cabinet/account/*/')
            finally:
                context.close()
                browser.close()
        self.assertTrue(ResidentAccess.objects.filter(
            user=self.recipient, account=self.account,
        ).exists())
        self.assertFalse(ResidentAccess.objects.filter(
            user=self.other, account=self.account,
        ).exists())

    def test_activation_error_and_controls_desktop_mobile_and_webkit(self):
        _, token = issue_invite(self.account, 'new-night-resident@example.test', 'owner')
        path = reverse('resident_invite', args=[token])
        with sync_playwright() as playwright:
            for engine, viewport, label in (
                (playwright.chromium, {'width': 1280, 'height': 900}, 'activation-desktop'),
                (playwright.chromium, {'width': 390, 'height': 844}, 'activation-mobile'),
                (playwright.webkit, {'width': 390, 'height': 844}, 'activation-webkit-mobile'),
            ):
                browser = engine.launch(headless=True)
                context = browser.new_context(viewport=viewport)
                try:
                    page = context.new_page()
                    page.goto(self.live_server_url + path, wait_until='networkidle')
                    page.locator('#id_password1').fill('night-activation-password')
                    page.locator('#id_password2').fill('does-not-match')
                    page.get_by_role('button', name='Создать защищённый доступ').click()
                    self.assertTrue(page.get_by_text('Пароли не совпадают.').is_visible())
                    self.assertEqual(page.locator('#id_password2').get_attribute('aria-invalid'), 'true')
                    self.assertGreaterEqual(page.locator('#id_password2').bounding_box()['height'], 44)
                    self.assertGreaterEqual(page.get_by_role('button').bounding_box()['height'], 44)
                    self._check_page(page, label)
                finally:
                    context.close()
                    browser.close()
        self.assertFalse(ResidentAccess.objects.filter(account=self.account).exists())
