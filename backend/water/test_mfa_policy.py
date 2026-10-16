"""Exercise real login/setup wizards and the shared Admin/Workspace boundary."""
from base64 import b32decode
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from .management.commands.setup_roles import ADMIN, LEGACY_ADMIN, OPERATOR, CONTROLLER, PRIVATE_REGISTRY
from .models import Account, ResidentAccess, User


@override_settings(TRUD_MFA_REQUIRED_USERNAMES=['admin'])
class AdminMFAPolicyTests(TestCase):
    password = 'synthetic-password-2026'
    protected_paths = ('/admin/', '/admin/water/account/', '/work/', '/work/accounts/')

    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', stdout=StringIO())
        cls.admins = [User.objects.create_superuser(username='AdMiN', password=cls.password)]
        cls.staff = [User.objects.create_superuser(username='mfa-root', password=cls.password)]
        for name in (ADMIN, LEGACY_ADMIN):
            user = User.objects.create_user(username=f'mfa-admin-{len(cls.staff)}',
                                            password=cls.password, is_staff=True)
            group, _ = Group.objects.get_or_create(name=name)
            if name == LEGACY_ADMIN:
                group.permissions.set(Group.objects.get(name=ADMIN).permissions.all())
            user.groups.add(group)
            cls.staff.append(user)
        for name in (OPERATOR, CONTROLLER, PRIVATE_REGISTRY):
            user = User.objects.create_user(username=f'mfa-staff-{len(cls.staff)}',
                                            password=cls.password, is_staff=True)
            user.groups.add(Group.objects.get(name=name))
            cls.staff.append(user)

    def password_login(self, user, target='/admin/'):
        return self.client.post(reverse('admin:login'), {
            'username': user.username, 'password': self.password, 'next': target,
        })

    def wizard_password(self, user, target):
        url = reverse('two_factor:login') + '?next=' + target
        self.client.get(url)
        return self.client.post(url, {
            'login_view-current_step': 'auth',
            'auth-username': user.username, 'auth-password': self.password,
        })

    def test_admin_password_login_cannot_read_until_setup(self):
        for user in self.admins:
            for target in self.protected_paths:
                with self.subTest(user=user.username, target=target):
                    self.client.logout()
                    self.password_login(user, target)
                    response = self.client.get(target, follow=True)
                    self.assertEqual(response.request['PATH_INFO'], reverse('two_factor:setup'))
                    self.assertEqual(response.status_code, 200)
                    self.assertFalse(TOTPDevice.objects.filter(user=user, confirmed=True).exists())

    def test_admin_wizard_without_device_reaches_setup(self):
        for target in ('/admin/', '/work/'):
            self.client.logout()
            response = self.wizard_password(self.admins[0], target)
            self.assertEqual(response.status_code, 302)
            response = self.client.get(response.url, follow=True)
            self.assertEqual(response.request['PATH_INFO'], reverse('two_factor:setup'))

    def test_confirmed_device_alone_does_not_verify_session(self):
        for user in self.admins:
            TOTPDevice.objects.create(user=user, name='default', confirmed=True)
            self.client.logout()
            self.password_login(user)
            for target in self.protected_paths:
                with self.subTest(user=user.username, target=target):
                    response = self.client.get(target, follow=True)
                    self.assertEqual(response.request['PATH_INFO'], reverse('two_factor:login'))
                    self.assertEqual(response.status_code, 200)

    def test_real_otp_login_unlocks_both_surfaces(self):
        for user in self.admins:
            with self.subTest(user=user.username):
                self.client.logout()
                device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
                response = self.wizard_password(user, '/work/')
                self.assertEqual(response.context['wizard']['steps'].current, 'token')
                self.assertNotIn('_auth_user_id', self.client.session)
                response = self.client.post(reverse('two_factor:login') + '?next=/work/', {
                    'login_view-current_step': 'token',
                    'token-otp_token': str(totp(device.bin_key)).zfill(6),
                })
                self.assertRedirects(response, '/work/')
                for target in self.protected_paths:
                    self.assertEqual(self.client.get(target).status_code, 200)

    def test_unconfirmed_device_requires_setup_and_setup_verifies_session(self):
        user = self.admins[0]
        pending = TOTPDevice.objects.create(user=user, confirmed=False)
        self.password_login(user)
        response = self.client.get('/work/', follow=True)
        setup_url = response.request['PATH_INFO'] + '?next=/work/'
        response = self.client.post(setup_url, {'setup_view-current_step': 'welcome'})
        self.assertEqual(response.context['wizard']['steps'].current, 'generator')
        key = b32decode(self.client.session['django_two_factor-qr_secret_key'])
        response = self.client.post(setup_url, {
            'setup_view-current_step': 'generator', 'generator-token': str(totp(key)).zfill(6),
        })
        self.assertRedirects(response, '/work/')
        self.assertIn(DEVICE_ID_SESSION_KEY, self.client.session)
        self.assertEqual(self.client.get('/admin/').status_code, 200)
        self.assertTrue(TOTPDevice.objects.filter(pk=pending.pk, confirmed=False).exists())

    def test_non_admin_staff_password_and_wizard_do_not_force_setup(self):
        for user in self.staff:
            for target in ('/admin/', '/work/'):
                with self.subTest(user=user.username, target=target):
                    self.client.logout()
                    self.assertRedirects(self.password_login(user, target), target)
                    self.client.logout()
                    self.assertRedirects(self.wizard_password(user, target), target)
                    self.assertFalse(TOTPDevice.objects.filter(user=user).exists())
        for user in self.staff[3:5]:
            self.client.force_login(user)
            self.assertEqual(self.client.get('/admin/water/user/').status_code, 403)

    def test_non_admin_existing_device_keeps_otp_wizard_login(self):
        user = self.staff[3]
        device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
        response = self.wizard_password(user, '/work/')
        self.assertEqual(response.context['wizard']['steps'].current, 'token')
        response = self.client.post(reverse('two_factor:login') + '?next=/work/', {
            'login_view-current_step': 'token', 'token-otp_token': str(totp(device.bin_key)).zfill(6),
        })
        self.assertRedirects(response, '/work/')
        self.assertTrue(TOTPDevice.objects.filter(pk=device.pk, confirmed=True).exists())

    def test_resident_password_access_stays_scoped(self):
        user = User.objects.create_user(username='mfa-resident', password=self.password)
        account = Account.objects.create(number='MFA-TEST', plot='Синтетический участок')
        ResidentAccess.objects.create(user=user, account=account, role='owner', starts=timezone.localdate())
        response = self.client.post(reverse('resident_login'), {
            'username': user.username, 'password': self.password,
        })
        self.assertRedirects(response, reverse('resident_dashboard'))
        self.assertEqual(self.client.get(reverse('resident_account', args=[account.pk])).status_code, 200)
        for target in ('/admin/', '/work/'):
            self.assertRedirects(self.client.get(target), reverse('admin:login') + '?next=' + target)
        self.assertFalse(TOTPDevice.objects.filter(user=user).exists())

    def test_admin_post_is_blocked_and_redirect_target_is_local(self):
        self.client.force_login(self.admins[0])
        response = self.client.post('/admin/water/account/add/', {'plot': 'Must not be saved'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Account.objects.exists())
        response = self.client.get(reverse('admin:login'), {'next': 'https://example.invalid/'})
        self.assertRedirects(response, reverse('two_factor:setup') + '?next=/admin/')

    def test_new_admin_role_does_not_require_otp_in_existing_password_session(self):
        user = self.staff[3]
        self.password_login(user)
        user.groups.add(Group.objects.get(name=ADMIN))
        self.assertEqual(self.client.get('/work/').status_code, 200)

    @override_settings(TRUD_MFA_REQUIRED_USERNAMES=['MFA-ROOT', 'mfa-staff-3'])
    def test_configured_usernames_require_setup_without_superuser_or_admin_group(self):
        for user in (self.staff[0], self.staff[3]):
            self.client.force_login(user)
            response = self.client.get('/work/', follow=True)
            self.assertEqual(response.request['PATH_INFO'], reverse('two_factor:setup'))
        self.client.force_login(self.admins[0])
        self.assertEqual(self.client.get('/work/').status_code, 200)

    @override_settings(TRUD_MFA_REQUIRED_USERNAMES=[])
    def test_empty_policy_allows_admin_password_access(self):
        self.assertRedirects(self.password_login(self.admins[0]), '/admin/')
