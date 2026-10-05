from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Account, ResidentAccess, User
from .portal import issue_invite


class InviteRecoveryTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='INVITE-RECOVERY', plot='Тестовый участок')
        self.recipient = User.objects.create_user(
            username='invite-recipient@example.test', email='invite-recipient@example.test',
            password='invite-recovery-password',
        )
        self.other = User.objects.create_user(username='other-cabinet@example.test')
        self.invite, token = issue_invite(self.account, self.recipient.email, 'owner')
        self.invite_url = reverse('resident_invite', args=[token])
        self.client = Client(enforce_csrf_checks=True)

    def test_wrong_cabinet_can_sign_out_then_finish_original_invite(self):
        self.client.force_login(self.other)
        response = self.client.get(self.invite_url)
        self.assertContains(response, 'Выйти и продолжить', status_code=403)
        self.assertContains(response, f'name="next" value="{self.invite_url}"', status_code=403)
        self.assertFalse(ResidentAccess.objects.filter(account=self.account).exists())
        self.invite.refresh_from_db()
        self.assertIsNone(self.invite.used_at)

        logout_url = reverse('resident_logout')
        self.assertEqual(self.client.post(logout_url, {'next': self.invite_url}).status_code, 403)
        csrf_token = self.client.cookies['csrftoken'].value
        response = self.client.post(logout_url, {
            'next': self.invite_url, 'csrfmiddlewaretoken': csrf_token,
        })
        self.assertRedirects(response, self.invite_url)
        response = self.client.get(self.invite_url)
        self.assertContains(response, 'Войти и продолжить')

        self.client.get(reverse('resident_login'), {'next': self.invite_url})
        response = self.client.post(reverse('resident_login'), {
            'username': self.recipient.username, 'password': 'invite-recovery-password',
            'next': self.invite_url, 'csrfmiddlewaretoken': self.client.cookies['csrftoken'].value,
        })
        self.assertRedirects(response, self.invite_url)
        self.assertFalse(ResidentAccess.objects.filter(account=self.account).exists())
        response = self.client.post(self.invite_url, {
            'csrfmiddlewaretoken': self.client.cookies['csrftoken'].value,
        })
        self.assertRedirects(response, reverse('resident_account', args=[self.account.pk]))
        self.assertTrue(ResidentAccess.objects.filter(
            account=self.account, user=self.recipient, starts=timezone.localdate(),
        ).exists())
        self.assertFalse(ResidentAccess.objects.filter(account=self.account, user=self.other).exists())
        self.invite.refresh_from_db()
        self.assertIsNotNone(self.invite.used_at)

    def test_logout_rejects_external_return_destination(self):
        self.client.force_login(self.other)
        self.client.get(self.invite_url)
        response = self.client.post(reverse('resident_logout'), {
            'next': 'https://example.test/untrusted',
            'csrfmiddlewaretoken': self.client.cookies['csrftoken'].value,
        })
        self.assertRedirects(response, reverse('resident_login'))
