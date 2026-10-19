"""Server-rendered help keeps role truth and navigation independent of JS."""
from html.parser import HTMLParser
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .access_policy import CAPABILITIES, ROLE_TEMPLATES
from .models import Account, ResidentAccess, User
from .staff_more import ROLES_GUIDE_UPDATED, ROLES_GUIDE_VERSION


class HelpMarkup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.help_items = []
        self.stack = []
        self.help_depth = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            self.links.append(attrs)
        if tag == 'aside' and 'data-help-key' in attrs:
            self.help_depth = len(self.stack)
        if tag == 'li' and self.help_depth is not None:
            self.help_items.append(attrs)
        if tag not in ('input', 'img', 'br', 'meta', 'link', 'use', 'path'):
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            position = len(self.stack) - 1 - self.stack[::-1].index(tag)
            if self.help_depth == position:
                self.help_depth = None
            self.stack = self.stack[:position]


@override_settings(TRUD_MFA_REQUIRED_USERNAMES=[])
class InternalHelpTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', stdout=StringIO())
        cls.manager = User.objects.create_user('synthetic-help-manager', is_staff=True)
        cls.manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        cls.resident = User.objects.create_user('synthetic-help-resident')
        cls.account = Account.objects.create(number='SYNTHETIC-HELP')
        ResidentAccess.objects.create(
            user=cls.resident, account=cls.account, role='owner', starts=timezone.localdate(),
        )

    def test_guide_lists_exactly_the_service_templates_and_their_functions(self):
        self.client.force_login(self.manager)
        page = self.client.get('/work/more/roles-guide/')
        roles = page.context['service_roles']
        expected = {code: role for code, role in ROLE_TEMPLATES.items() if code != 'resident_account'}
        self.assertEqual({role['code'] for role in roles}, set(expected))
        for role in roles:
            self.assertEqual(role['label'], expected[role['code']].label)
            self.assertEqual(role['functions'], [CAPABILITIES[code].label for code in expected[role['code']].capabilities])
            self.assertContains(page, f'<summary>{role["label"]}</summary>', html=True)
        self.assertContains(page, ROLES_GUIDE_VERSION)
        self.assertContains(page, ROLES_GUIDE_UPDATED)
        self.assertContains(page, 'TRUD_MFA_REQUIRED_USERNAMES')
        self.assertContains(page, 'Повторный код перед каждой такой операцией не запрашивается.')
        self.assertContains(page, 'Иванов Иван Иванович')
        self.assertNotContains(page, 'новая панель в меню')

    def test_home_more_and_profile_have_short_visible_help_without_js(self):
        self.client.force_login(self.manager)
        for url in ['/work/', '/work/more/', '/work/water/', '/work/finance/', '/work/documents/',
                    '/work/governance/', '/work/security/', reverse('personal_profile')]:
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, 'Что здесь можно сделать', count=1)
                parser = HelpMarkup(page.content.decode())
                self.assertGreaterEqual(len(parser.help_items), 2)
                self.assertLessEqual(len(parser.help_items), 4)
                self.assertContains(page, 'ui-hints.js', count=1)
                self.assertContains(page, 'password-visibility.js', count=1)
        self.client.force_login(self.resident)
        for url in [reverse('resident_plots'), reverse('resident_account', args=[self.account.pk]),
                    reverse('resident_more', args=[self.account.pk]), reverse('personal_profile')]:
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, 'Что здесь можно сделать', count=1)
                self.assertContains(page, '<details open>')
                self.assertContains(page, 'data-help-key=')

    def test_primary_navigation_has_descriptions_before_javascript_runs(self):
        self.client.force_login(self.manager)
        page = self.client.get('/work/')
        parser = HelpMarkup(page.content.decode())
        routes = ['/work/', '/work/more/', '/work/more/#residents', '/work/more/#operations',
                  '/work/more/#tsn', reverse('personal_profile')]
        for route in routes:
            links = [link for link in parser.links if link.get('href') == route and 'data-hint' in link]
            self.assertTrue(links, route)
            self.assertTrue(all(link.get('title') and link['data-hint'] for link in links))
        self.assertContains(page, 'class="nav-hint"')
        more = self.client.get('/work/more/')
        self.assertContains(more, 'class="hint-menu-group"', count=3)
