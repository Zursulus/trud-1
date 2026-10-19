"""Rendered password/email fields keep their browser autofill semantics."""
from html.parser import HTMLParser
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Account, ResidentAccess, User
from .portal import issue_invite, issue_password_reset


class Inputs(HTMLParser):
    def __init__(self, response):
        super().__init__()
        self.fields = {}
        self.feed(response.content.decode())

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and 'name' in attrs:
            self.fields[attrs['name']] = attrs


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordFieldTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            'password-field-resident', email='old@example.test', password='test-password',
        )
        cls.staff = User.objects.create_superuser(
            'password-field-staff', email='staff@example.test', password='test-password',
        )
        cls.account = Account.objects.create(number='PASSWORD-FIELDS')
        ResidentAccess.objects.create(
            user=cls.user, account=cls.account, role='owner', starts=timezone.localdate(),
        )

    def setUp(self):
        cache.clear()

    def page(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'water/password-visibility.js', count=1)
        self.assertContains(response, 'water/password-visibility.css', count=1)
        return Inputs(response).fields

    def assert_password(self, fields, name, autocomplete):
        self.assertEqual(fields[name]['type'], 'password')
        self.assertEqual(fields[name]['autocomplete'], autocomplete)

    def test_email_binding_and_code_fields(self):
        self.client.force_login(self.user)
        url = reverse('profile_email')
        fields = self.page(url)
        for name, value in {
            'type': 'email', 'autocomplete': 'email', 'inputmode': 'email',
            'autocapitalize': 'off', 'spellcheck': 'false',
        }.items():
            self.assertEqual(fields['email'][name], value)
        self.assert_password(fields, 'password', 'current-password')
        with patch('allauth.account.adapter.DefaultAccountAdapter.generate_email_verification_code',
                   return_value='123456'):
            self.assertRedirects(self.client.post(url, {
                'email': 'new@example.test', 'password': 'test-password',
            }), url)
        fields = self.page(url)
        self.assertEqual(fields['code']['autocomplete'], 'one-time-code')
        self.assertEqual(fields['code']['inputmode'], 'numeric')

    def test_resident_login(self):
        fields = self.page(reverse('resident_login'))
        self.assertEqual(fields['username']['autocomplete'], 'username')
        self.assert_password(fields, 'password', 'current-password')

    def test_password_change(self):
        self.client.force_login(self.user)
        fields = self.page(reverse('resident_password_change'))
        self.assert_password(fields, 'old_password', 'current-password')
        for name in ('new_password1', 'new_password2'):
            self.assert_password(fields, name, 'new-password')

    def test_invitation_and_reset(self):
        _, invite = issue_invite(self.account, 'invited@example.test', 'owner')
        _, reset = issue_password_reset(self.user)
        for name, token in [('resident_invite', invite), ('resident_password_reset', reset)]:
            with self.subTest(page=name):
                fields = self.page(reverse(name, args=[token]))
                for field in ('password1', 'password2'):
                    self.assert_password(fields, field, 'new-password')

    def test_project_mfa_login(self):
        fields = self.page(reverse('two_factor:login'))
        self.assertEqual(fields['auth-username']['autocomplete'], 'username')
        self.assert_password(fields, 'auth-password', 'current-password')

    def test_admin_password_form_and_workspace_base(self):
        self.client.force_login(self.staff)
        fields = self.page(reverse('admin:password_change'))
        self.assert_password(fields, 'old_password', 'current-password')
        for name in ('new_password1', 'new_password2'):
            self.assert_password(fields, name, 'new-password')
        self.page(reverse('staff_workspace:home'))

    def test_stock_admin_login_is_unchanged(self):
        response = self.client.get(reverse('admin:login'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'water/password-visibility.js')
