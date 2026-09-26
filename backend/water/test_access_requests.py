from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from .access_requests import ResidentAccessRequest
from .models import Account, Person, ResidentAccess, ResidentInvite, User
from .resident_models import TsnMembership


class PublicAccessRequestTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='TEST-ACCESS', plot='Садовая 10')
        self.payload = {
            'full_name': 'Тестовый Заявитель',
            'email': 'requester@example.test',
            'phone': '+7 900 000-00-00',
            'plot_hint': 'Садовая 10',
            'claimed_role': ResidentAccessRequest.CLAIM_OWNER,
            'message': 'Синтетическая заявка ZUR-69',
            'website': '',
        }

    def test_matching_and_unknown_plot_get_same_external_response_without_auto_grant(self):
        matching = self.client.post(
            reverse('resident_access_request'), self.payload, REMOTE_ADDR='198.51.100.10',
        )
        unknown_payload = {
            **self.payload,
            'email': 'unknown@example.test',
            'plot_hint': 'Несуществующий тестовый участок',
        }
        unknown = self.client.post(
            reverse('resident_access_request'), unknown_payload, REMOTE_ADDR='198.51.100.11',
        )

        self.assertEqual(matching.status_code, 302)
        self.assertEqual(unknown.status_code, 302)
        self.assertEqual(matching.url, reverse('resident_access_request_sent'))
        self.assertEqual(unknown.url, reverse('resident_access_request_sent'))
        self.assertEqual(ResidentAccessRequest.objects.count(), 2)
        self.assertEqual(ResidentAccess.objects.count(), 0)
        self.assertEqual(Person.objects.count(), 0)
        self.assertEqual(TsnMembership.objects.count(), 0)
        self.assertEqual(User.objects.filter(is_staff=False).count(), 0)

    def test_rate_limit_and_honeypot_keep_same_neutral_response(self):
        responses = [
            self.client.post(
                reverse('resident_access_request'), self.payload, REMOTE_ADDR='198.51.100.20',
            )
            for _ in range(4)
        ]
        self.assertTrue(all(response.status_code == 302 for response in responses))
        self.assertTrue(all(response.url == reverse('resident_access_request_sent') for response in responses))
        self.assertEqual(ResidentAccessRequest.objects.count(), 3)

        bot_payload = {**self.payload, 'website': 'https://spam.example.test'}
        bot = self.client.post(
            reverse('resident_access_request'), bot_payload, REMOTE_ADDR='198.51.100.21',
        )
        self.assertEqual(bot.status_code, 302)
        self.assertEqual(bot.url, reverse('resident_access_request_sent'))
        self.assertEqual(ResidentAccessRequest.objects.count(), 3)

    def test_technical_key_does_not_store_raw_email_or_ip(self):
        self.client.post(
            reverse('resident_access_request'), self.payload, REMOTE_ADDR='198.51.100.30',
        )
        request_obj = ResidentAccessRequest.objects.get()
        self.assertNotIn(self.payload['email'], request_obj.submission_key)
        self.assertNotIn('198.51.100.30', request_obj.submission_key)

    def test_original_submission_is_immutable(self):
        self.client.post(
            reverse('resident_access_request'), self.payload, REMOTE_ADDR='198.51.100.40',
        )
        request_obj = ResidentAccessRequest.objects.get()
        request_obj.email = 'rewritten@example.test'
        with self.assertRaises(ValidationError):
            request_obj.save()

    def test_confirmation_page_is_neutral(self):
        response = self.client.get(reverse('resident_access_request_sent'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'не подтверждает наличие записи')
        self.assertNotContains(response, self.account.plot)


class AccessRequestReviewTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='TEST-REVIEW', plot='Лесная 8')
        self.request_obj = ResidentAccessRequest.objects.create(
            full_name='Тестовый Заявитель',
            email='reviewer-target@example.test',
            phone='',
            plot_hint='Лесная 8',
            claimed_role=ResidentAccessRequest.CLAIM_OWNER,
            message='Синтетическая заявка для проверки',
            submission_key='synthetic-review-key',
        )
        self.staff = User.objects.create_user(
            username='access-reviewer', password='test-password', is_staff=True,
        )
        for codename in (
            'access_private_registry',
            'view_residentaccessrequest',
            'change_residentaccessrequest',
            'add_residentinvite',
        ):
            self.staff.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(self.staff)

    def test_approval_creates_invite_but_not_resident_access(self):
        response = self.client.post(
            reverse('admin:water_residentaccessrequest_approve', args=[self.request_obj.pk]),
            {
                'account': self.account.pk,
                'role': 'owner',
                'email': self.request_obj.email,
                'decision_note': 'Основание проверено на синтетических данных',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Создана одноразовая ссылка')

        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ResidentAccessRequest.STATUS_APPROVED)
        self.assertEqual(self.request_obj.matched_account_id, self.account.pk)
        self.assertEqual(self.request_obj.approved_role, 'owner')
        self.assertEqual(self.request_obj.decided_by_id, self.staff.pk)
        self.assertIsNotNone(self.request_obj.decided_at)
        self.assertIsNotNone(self.request_obj.invite_id)
        self.assertEqual(ResidentInvite.objects.count(), 1)
        self.assertEqual(ResidentAccess.objects.count(), 0)
        self.assertEqual(Person.objects.count(), 0)
        self.assertEqual(TsnMembership.objects.count(), 0)

        second = self.client.post(
            reverse('admin:water_residentaccessrequest_approve', args=[self.request_obj.pk]),
            {
                'account': self.account.pk,
                'role': 'owner',
                'email': self.request_obj.email,
                'decision_note': 'Повторное решение не должно сработать',
            },
        )
        self.assertEqual(second.status_code, 302)
        self.assertEqual(ResidentInvite.objects.count(), 1)

    def test_rejection_is_audited_and_creates_no_invite(self):
        response = self.client.post(
            reverse('admin:water_residentaccessrequest_reject', args=[self.request_obj.pk]),
            {'decision_note': 'Синтетическое основание не подтверждено'},
        )
        self.assertEqual(response.status_code, 302)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ResidentAccessRequest.STATUS_REJECTED)
        self.assertEqual(self.request_obj.decided_by_id, self.staff.pk)
        self.assertIsNotNone(self.request_obj.decided_at)
        self.assertEqual(ResidentInvite.objects.count(), 0)
        self.assertEqual(ResidentAccess.objects.count(), 0)

    def test_review_requires_private_registry_permission(self):
        outsider = User.objects.create_user(
            username='ordinary-staff', password='test-password', is_staff=True,
        )
        for codename in ('view_residentaccessrequest', 'change_residentaccessrequest', 'add_residentinvite'):
            outsider.user_permissions.add(Permission.objects.get(codename=codename))
        self.client.force_login(outsider)

        response = self.client.get(
            reverse('admin:water_residentaccessrequest_approve', args=[self.request_obj.pk]),
        )
        self.assertEqual(response.status_code, 403)
