import time
from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth.models import Group
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from water.models import User
from .access import can_write
from .models import Profile
from .profile import SESSION_KEY


@override_settings(FORUM_ENABLED=False, RESIDENT_EMAIL_LOGIN_ENABLED=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ProfileTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('profile-user', password='test-password', email='old@example.test')
        self.client.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        self.url = reverse('profile_email')

    def send(self, email='new@example.test', password='test-password', code='123456'):
        with patch('allauth.account.adapter.DefaultAccountAdapter.generate_email_verification_code', return_value=code):
            return self.client.post(self.url, {'email': email, 'password': password})

    def test_profile_without_plot_or_forum(self):
        response = self.client.get(reverse('personal_profile'))
        self.assertContains(response, 'Второй фактор')
        self.assertContains(response, reverse('resident_password_change'))
        self.assertContains(response, reverse('profile_email'))
        self.assertEqual(self.client.post(reverse('personal_profile'), {'nickname': 'Морской сосед'}).status_code, 302)
        self.assertEqual(Profile.objects.get(user=self.user).nickname, 'Морской сосед')

    def test_bind_code_no_login_and_replay(self):
        backend = self.client.session['_auth_user_backend']
        self.assertRedirects(self.send(), self.url)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'old@example.test')
        self.assertIn('123456', mail.outbox[-1].body)
        self.assertEqual(mail.outbox[-1].to, ['new@example.test'])
        self.assertFalse(can_write(self.user))
        self.assertEqual(self.client.post(self.url, {'code': 'bad'}).status_code, 200)
        self.assertRedirects(self.client.post(self.url, {'code': '123456'}), reverse('personal_profile'))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'new@example.test')
        self.assertTrue(EmailAddress.objects.get(user=self.user, email=self.user.email).verified)
        self.assertTrue(can_write(self.user))
        self.assertEqual(self.client.session['_auth_user_backend'], backend)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.client.post(self.url, {'code': '123456'})
        self.assertEqual(EmailAddress.objects.filter(user=self.user, verified=True).count(), 1)

    def test_staff_binding_does_not_enable_code_login(self):
        self.user.is_staff = True
        self.user.save()
        self.assertRedirects(self.send(), self.url)
        self.assertRedirects(self.client.post(self.url, {'code': '123456'}), reverse('personal_profile'))
        self.assertEqual(self.client.session['_auth_user_backend'], 'django.contrib.auth.backends.ModelBackend')
        with override_settings(RESIDENT_EMAIL_LOGIN_ENABLED=True):
            self.assertEqual(self.client.get(reverse('account_login')).status_code, 403)
            self.client.logout()
            self.client.post(reverse('account_login'), {'email': 'new@example.test'})
            self.assertNotIn('_auth_user_id', self.client.session)
            self.assertEqual(len(mail.outbox), 1)

    @override_settings(TRUD_MFA_REQUIRED_USERNAMES=['profile-user'])
    def test_required_staff_mfa(self):
        self.user.is_staff = True
        self.user.save()
        response = self.send()
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/', response.url)
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(len(mail.outbox), 0)

    def test_wrong_password(self):
        self.assertContains(self.send(password='bad'), 'Проверьте текущий пароль')
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(len(mail.outbox), 0)

    def test_collision_in_user_or_email_address(self):
        other = User.objects.create_user('other', email='taken@example.test')
        EmailAddress.objects.create(user=other, email='alias@example.test', verified=False)
        for target in ['TAKEN@example.test', 'alias@example.test']:
            self.assertContains(self.send(email=target), 'Этот адрес недоступен')
        self.assertEqual(len(mail.outbox), 0)

    def test_collision_after_send(self):
        self.send()
        User.objects.create_user('new-holder', email='NEW@example.test')
        response = self.client.post(self.url, {'code': '123456'})
        self.assertContains(response, 'Не удалось привязать адрес')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'old@example.test')
        self.assertFalse(EmailAddress.objects.filter(user=self.user, verified=True).exists())

    def test_expiry(self):
        self.send()
        session = self.client.session
        state = session[SESSION_KEY]
        state['at'] = time.time() - 601
        session[SESSION_KEY] = state
        session.save()
        self.client.post(self.url, {'code': '123456'})
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'old@example.test')

    def test_three_wrong_codes_abort(self):
        self.send()
        for _ in range(3):
            self.client.post(self.url, {'code': 'wrong'})
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.client.post(self.url, {'code': '123456'})
        self.assertFalse(EmailAddress.objects.filter(user=self.user, verified=True).exists())

    def test_rate_limit_password_attempts(self):
        for _ in range(5):
            self.send(password='bad')
        self.assertContains(self.send(), 'Слишком много попыток')
        self.assertEqual(len(mail.outbox), 0)

    def test_cancel_and_resend_old_code_invalid(self):
        self.send()
        self.client.post(self.url, {'action': 'cancel'})
        cache.clear()  # Model elapsed mail cooldown, not a production bypass.
        self.send(code='654321')
        self.client.post(self.url, {'code': '123456'})
        self.assertFalse(EmailAddress.objects.filter(user=self.user, verified=True).exists())
        self.assertRedirects(self.client.post(self.url, {'code': '654321'}), reverse('personal_profile'))

    def test_current_address_confirmation(self):
        self.send(email='OLD@example.test')
        self.client.post(self.url, {'code': '123456'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'old@example.test')
        self.assertTrue(can_write(self.user))

    def test_changed_user_invalidates_pending(self):
        self.send()
        self.user.email = 'changed@example.test'
        self.user.save()
        self.client.post(self.url, {'code': '123456'})
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertFalse(EmailAddress.objects.filter(user=self.user, verified=True).exists())

    def test_other_session_cannot_confirm(self):
        self.send()
        other = Client()
        other.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        other.post(self.url, {'code': '123456'})
        self.assertFalse(EmailAddress.objects.filter(user=self.user, verified=True).exists())

    def test_passwordless_proves_old_and_new_addresses(self):
        self.user.set_unusable_password()
        self.user.save()
        self.client.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        self.send()
        self.assertEqual(mail.outbox[-1].to, ['old@example.test'])
        with patch('allauth.account.adapter.DefaultAccountAdapter.generate_email_verification_code', return_value='654321'):
            self.client.post(self.url, {'code': '123456'})
        self.assertEqual(mail.outbox[-1].to, ['new@example.test'])
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'old@example.test')
        self.client.post(self.url, {'code': '654321'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'new@example.test')
        self.assertFalse(self.user.has_usable_password())

    def test_guest_and_csrf(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user, backend='django.contrib.auth.backends.ModelBackend')
        for url in [self.url, reverse('personal_profile')]:
            self.assertEqual(client.post(url, {'email': 'new@example.test'}).status_code, 403)

    def test_legacy_email_route_is_only_a_protected_profile_transition(self):
        self.assertRedirects(self.client.get(reverse('account_email')), self.url)
        self.assertEqual(self.client.post(reverse('account_email'), {'email': 'new@example.test'}).status_code, 405)
        self.client.logout()
        response = self.client.get(reverse('account_email'), follow=True)
        self.assertEqual(response.request['PATH_INFO'], reverse('resident_login'))
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertFalse(EmailAddress.objects.filter(verified=True).exists())
