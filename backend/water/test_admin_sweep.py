from io import StringIO

from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import Account, User


class AdminSweepTests(TestCase):
    def setUp(self):
        call_command('setup_roles', stdout=StringIO())
        self.superuser = User.objects.create_superuser(
            username='admin-sweep-superuser',
            email='admin-sweep@example.test',
            password='admin-sweep-password-2026!',
        )
        self.manager = User.objects.create_user(
            username='admin-sweep-manager',
            is_staff=True,
        )
        self.manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        self.account = Account.objects.create(number='ADMIN-SWEEP-1', plot='Тестовый участок')
        self.factory = RequestFactory()

    def login_as(self, user):
        device, _ = TOTPDevice.objects.get_or_create(user=user, defaults={'name': 'admin sweep device'})
        self.client.force_login(user)
        session = self.client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()

    def request_for(self, user):
        request = self.factory.get('/admin/')
        request.user = user
        return request

    def assert_get_ok(self, url, label):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, f'{label}: {url} -> {response.status_code}')
        return response

    def test_superuser_all_registered_admin_lists_add_forms_and_existing_changes_render(self):
        self.login_as(self.superuser)
        request = self.request_for(self.superuser)
        checked = 0

        for model, model_admin in sorted(admin.site._registry.items(), key=lambda item: item[0]._meta.label_lower):
            opts = model._meta
            label = opts.label_lower
            if not model_admin.has_module_permission(request):
                continue

            if model_admin.has_view_permission(request):
                self.assert_get_ok(
                    reverse(f'admin:{opts.app_label}_{opts.model_name}_changelist'),
                    f'{label} changelist',
                )
                checked += 1

            if model_admin.has_add_permission(request):
                self.assert_get_ok(
                    reverse(f'admin:{opts.app_label}_{opts.model_name}_add'),
                    f'{label} add',
                )
                checked += 1

            obj = model_admin.get_queryset(request).order_by(opts.pk.name).first()
            if obj is not None and model_admin.has_view_or_change_permission(request, obj):
                self.assert_get_ok(
                    reverse(f'admin:{opts.app_label}_{opts.model_name}_change', args=[obj.pk]),
                    f'{label} change',
                )
                checked += 1

        self.assertGreater(checked, 30)

    def test_superuser_custom_admin_workspaces_render(self):
        self.login_as(self.superuser)
        urls = (
            '/admin/public/content/',
            '/admin/water/balance/',
            '/admin/water/controller-workspace/',
            '/admin/water/package-dry-run/',
            '/admin/water/readings/review/',
            '/admin/water/readings/export-xlsx/',
            '/admin/water/importbatch/upload/',
            reverse('admin:water_account_statement', args=[self.account.pk]),
            reverse('admin:water_account_invite', args=[self.account.pk]),
        )
        for url in urls:
            self.assert_get_ok(url, 'custom admin workspace')

    def test_manager_dashboard_entry_points_do_not_fail(self):
        self.login_as(self.manager)
        response = self.assert_get_ok('/admin/', 'manager dashboard')
        for label in (
            'Показания на проверке',
            'Новости сайта',
            'Публичные документы',
            'Журнал показаний',
            'Участки',
            'Счётчики',
            'Начисления',
            'Документы жителей',
        ):
            self.assertContains(response, label)
