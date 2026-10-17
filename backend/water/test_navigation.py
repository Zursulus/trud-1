from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import Account, ResidentAccess, User


@override_settings(FORUM_ENABLED=True, FORUM_PUBLIC_READ=True)
class NavigationTests(TestCase):
    def menu(self, user=None):
        if user:
            self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('resident_access_help'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="Общая навигация"', count=1)
        self.assertContains(response, 'aria-label="Хлебные крошки"')
        return {item['label'] for item in response.context['internal_nav']}

    def test_guest(self):
        self.assertEqual(self.menu(), {'Сайт', 'Обсуждения', 'Вход', 'Помощь'})

    @override_settings(FORUM_ENABLED=False)
    def test_disabled_forum(self):
        self.assertNotIn('Обсуждения', self.menu())

    @override_settings(FORUM_PUBLIC_READ=False)
    def test_private_forum_guest(self):
        self.assertNotIn('Обсуждения', self.menu())

    def test_no_plot_and_staff_roles(self):
        for name, staff, superuser in [('no-plot', False, False), ('controller', True, False),
                                         ('staff', True, False), ('superuser', True, True)]:
            with self.subTest(role=name):
                user = User.objects.create_user(name, is_staff=staff, is_superuser=superuser)
                if name == 'controller':
                    user.user_permissions.add(Permission.objects.get(codename='use_controller_workspace'))
                menu = self.menu(user)
                self.assertIn('Профиль', menu)
                self.assertNotIn('Мой кабинет', menu)
                self.assertEqual('Управление' in menu, staff)
                self.assertIn('Запросить доступ', menu)

    def test_resident(self):
        user = User.objects.create_user('resident')
        account = Account.objects.create(number='NAV-TEST')
        ResidentAccess.objects.create(user=user, account=account, role="owner", starts=timezone.localdate())
        menu = self.menu(user)
        self.assertIn('Мой кабинет', menu)
        response = self.client.get(reverse('resident_access_help'))
        cabinet = next(item for item in response.context['internal_nav'] if item['label'] == 'Мой кабинет')
        self.assertEqual(cabinet['url'], reverse('resident_plots'))
        self.assertNotIn('Управление', menu)
        self.assertNotIn('Запросить доступ', menu)

    def test_links_do_not_authorize_nonstaff(self):
        user = User.objects.create_user('outsider')
        self.menu(user)
        for url in ['/work/', '/work/access/', '/work/forum/', '/admin/water/user/']:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/admin/login/', response.url)

    def test_board_member_without_plot(self):
        from .board_polls import BoardMembership
        user = User.objects.create_user('board-member')
        BoardMembership.objects.create(user=user, starts=timezone.localdate())
        menu = self.menu(user)
        self.assertIn('Опросы правления', menu)
        self.assertNotIn('Управление', menu)
        response = self.client.get(reverse('board_poll_home'))
        self.assertContains(response, reverse('personal_profile'))
        self.assertNotContains(response, 'href="/admin/"')

    def test_inactive_user_gets_no_private_navigation(self):
        user = User.objects.create_user('disabled', is_active=False, is_staff=True)
        self.assertNotIn('Управление', self.menu(user))

    def test_management_catalog_keeps_permissions_separate(self):
        user = User.objects.create_user('section-creator', is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='add_section'))
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get('/work/')
        self.assertEqual(response.status_code, 200)
        links = {item['label']: item['url'] for item in response.context['management_links']}
        self.assertIn('Техническая админка', links)
        self.assertIn('Создать раздел форума', links)
        for hidden in ['Модерация форума', 'Разделы форума', 'Доступы жителей', 'Пользователи', 'Начисления и оплаты']:
            self.assertNotIn(hidden, links)
        self.assertEqual(self.client.get(reverse('admin:forum_section_add')).status_code, 200)
        self.assertEqual(self.client.get('/work/forum/').status_code, 403)
        self.assertEqual(self.client.get('/work/access/').status_code, 403)
        self.assertEqual(self.client.get(reverse('admin:water_user_changelist')).status_code, 403)

    def test_superuser_management_and_common_shells(self):
        user = User.objects.create_user('technical-user', is_staff=True, is_superuser=True)
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get('/work/')
        self.assertEqual(response.status_code, 200)
        links = {item['label']: item['url'] for item in response.context['management_links']}
        for label in ['Пользователи', 'Техническая админка', 'Модерация форума', 'Создать раздел форума', 'Доступы жителей']:
            self.assertIn(label, links)
        for label, url in links.items():
            with self.subTest(management=label):
                self.assertEqual(self.client.get(url).status_code, 200)
        for route in ['forum:index', 'personal_profile', 'profile_email', 'two_factor:profile', 'admin:index']:
            with self.subTest(route=route):
                page = self.client.get(reverse(route))
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, 'aria-label="Общая навигация"', count=1)
                self.assertContains(page, reverse('staff_workspace:home'))
                self.assertContains(page, 'internal-nav.css')
                self.assertContains(page, 'internal-nav.js')

    @override_settings(FORUM_ENABLED=False)
    def test_disabled_forum_hides_management_actions(self):
        from .navigation import management_links
        user = User.objects.create_user('all-but-forum', is_staff=True, is_superuser=True)
        links = management_links(user)
        self.assertFalse(any('форума' in item['label'] for item in links))
        self.assertEqual(self.client.get(reverse('forum:index')).status_code, 404)

    def test_denial_has_escape_routes(self):
        user = User.objects.create_user('denied-resident')
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.get(reverse('resident_dashboard'))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, reverse('personal_profile'), status_code=403)
        self.assertContains(response, reverse('forum:index'), status_code=403)
        self.assertNotContains(response, 'href="/work/"', status_code=403)


    def test_import_links_require_the_private_registry_boundary(self):
        from .navigation import management_links
        user = User.objects.create_user('import-limited', is_staff=True)
        user.user_permissions.add(Permission.objects.get(codename='add_importbatch'))
        user.user_permissions.add(Permission.objects.get(codename='view_importbatch'))
        self.assertFalse(any('импорт' in item['label'].lower() for item in management_links(user)))
