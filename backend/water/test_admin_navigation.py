from io import StringIO

from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import User


class AdminNavigationTests(TestCase):
    def setUp(self):
        call_command('setup_roles', stdout=StringIO())
        self.manager = User.objects.create_user(username='navigation-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        self.private_registry_user = User.objects.create_user(username='navigation-private-registry', is_staff=True)
        self.private_registry_user.groups.add(Group.objects.get(name='Закрытый реестр членов ТСН'))
        self.controller = User.objects.create_user(username='navigation-controller', is_staff=True)
        self.controller.groups.add(Group.objects.get(name='Контролёр воды'))

    def login_as(self, user):
        device, _ = TOTPDevice.objects.get_or_create(user=user, defaults={'name': 'navigation test device'})
        self.client.force_login(user)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()

    def test_admin_uses_simplified_dashboard_without_sidebar(self):
        self.assertEqual(admin.site.index_template, 'admin/water/index.html')
        self.assertFalse(admin.site.enable_nav_sidebar)

    def test_manager_sees_operational_workflows_but_not_private_registry(self):
        self.login_as(self.manager)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        for label in (
            'Показания на проверке', 'Новости сайта', 'Публичные документы',
            'Журнал показаний', 'Участки', 'Счётчики', 'Начисления',
            'Документы жителей', 'Настройки / служебное',
        ):
            self.assertContains(response, label)
        self.assertNotContains(response, 'Закрытый реестр членов')
        self.assertContains(response, '?status__exact=pending')
        self.assertContains(response, '/admin/public_site/publicnews/')
        self.assertContains(response, '/admin/public_site/publicdocument/')

    def test_private_registry_user_sees_closed_registry_entry_point(self):
        self.login_as(self.private_registry_user)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Закрытый реестр членов')
        self.assertContains(response, '/admin/water/memberregistryentry/')

    def test_controller_sees_capture_but_not_manager_sections(self):
        self.login_as(self.controller)
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Внести показание')
        self.assertNotContains(response, 'Начисления')
        self.assertNotContains(response, 'Показания на проверке')
        self.assertNotContains(response, 'Новости сайта')
        self.assertNotContains(response, 'Публичные документы')
        self.assertNotContains(response, 'Закрытый реестр членов')
