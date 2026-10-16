from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .access_requests import ResidentAccessRequest
from .models import Account, Person, ResidentAccess, ResidentInvite, User
from .access_workflow import approve_access_request
from .resident_models import TsnMembership


class PrivateAccessRequestTests(TestCase):
    def setUp(self):
        from forum.test_helpers import consent_payload, record_consents
        self.account = Account.objects.create(number='TEST-ACCESS', plot='Садовая 10')
        self.resident = User.objects.create_user(username='requester', email='requester@example.test')
        record_consents(self.resident)
        self.client.force_login(self.resident)
        self.payload = {
            'full_name': 'Тестовый Заявитель', 'phone': '', 'plot_hint': 'Садовая 10',
            'claimed_role': ResidentAccessRequest.CLAIM_OWNER, 'message': '', 'website': '',
            **consent_payload(binding=True),
        }

    def test_login_required(self):
        self.client.logout()
        response = self.client.post(reverse('resident_access_request'), self.payload)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ResidentAccessRequest.objects.exists())

    def test_matching_and_unknown_plot_never_auto_grant(self):
        for plot in ('Садовая 10', 'Неизвестный участок'):
            response = self.client.post(reverse('resident_access_request'), {**self.payload, 'plot_hint': plot})
            self.assertRedirects(response, reverse('resident_access_request_sent'))
        self.assertEqual(ResidentAccessRequest.objects.count(), 2)
        self.assertFalse(ResidentAccess.objects.exists())
        self.assertFalse(Person.objects.exists())
        self.assertFalse(TsnMembership.objects.exists())
        self.assertEqual(User.objects.filter(is_staff=False).count(), 1)

    def test_rate_limit_and_honeypot(self):
        for _ in range(4):
            self.client.post(reverse('resident_access_request'), self.payload)
        self.assertEqual(ResidentAccessRequest.objects.count(), 3)
        self.client.post(reverse('resident_access_request'), {**self.payload, 'website': 'bot'})
        self.assertEqual(ResidentAccessRequest.objects.count(), 3)

    def test_submission_is_bound_to_login_and_consent(self):
        self.client.post(reverse('resident_access_request'), {
            **self.payload, 'email': 'forged@example.test', 'last_reading': '123.456',
        }, REMOTE_ADDR='198.51.100.30')
        obj = ResidentAccessRequest.objects.get()
        self.assertEqual(obj.requester_user, self.resident)
        self.assertEqual(obj.email, self.resident.email)
        self.assertEqual(str(obj.last_reading), '123.456')
        self.assertEqual(obj.consent_record.user, self.resident)
        self.assertEqual(obj.consent_record.document_type, 'binding')
        self.assertNotIn(self.resident.email, obj.submission_key)
        self.assertNotIn('198.51.100.30', obj.submission_key)
        obj.email = 'rewritten@example.test'
        with self.assertRaises(ValidationError):
            obj.save()

    def test_separate_binding_consent_required(self):
        self.payload.pop('consent')
        response = self.client.post(reverse('resident_access_request'), self.payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ResidentAccessRequest.objects.exists())

    def test_status_and_rejection_reason_visible_only_to_applicant(self):
        self.client.post(reverse('resident_access_request'), self.payload)
        obj = ResidentAccessRequest.objects.get()
        reviewer = User.objects.create_superuser('reviewer', 'reviewer@example.test', 'test-password')
        from .access_workflow import reject_access_request
        reject_access_request(obj.pk, decision_note='Не совпало ФИО', actor=reviewer)
        response = self.client.get(reverse('resident_access_request_sent'))
        self.assertContains(response, 'Отклонена')
        self.assertContains(response, 'Не совпало ФИО')
        other = User.objects.create_user('other', email='other@example.test')
        self.client.force_login(other)
        self.assertNotContains(self.client.get(reverse('resident_access_request_sent')), 'Не совпало ФИО')


class InviteDisambiguationTests(TestCase):
    def test_bound_request_activates_exact_existing_cabinet_despite_duplicate_contact_email(self):
        first = Account.objects.create(number='INVITE-A', plot='Участок A')
        second = Account.objects.create(number='INVITE-B', plot='Участок B')
        resident = User.objects.create_user(username='test333', password='test-password')
        ResidentAccess.objects.create(
            user=resident, account=first, role='payer', starts=timezone.localdate(),
        )
        duplicate_one = User.objects.create_user(
            username='technical-one', email='shared@example.test', password='test-password', is_staff=True,
        )
        User.objects.create_user(
            username='technical-two', email='shared@example.test', password='test-password', is_staff=True,
        )
        request_obj = ResidentAccessRequest.objects.create(
            full_name='Тестовый плательщик', email='shared@example.test', phone='',
            plot_hint='Участок B', claimed_role=ResidentAccessRequest.CLAIM_PAYER,
            message='', submission_key='bound-request', requester_user=resident,
        )
        actor = User.objects.create_user(username='reviewer', is_staff=True)
        actor.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='water',
            codename__in=['access_private_registry', 'change_residentaccessrequest', 'add_residentinvite'],
        ))
        _decided, _invite, raw = approve_access_request(
            request_obj.pk, account=second, email=request_obj.email, role='payer',
            decision_note='Основание проверено', actor=actor,
        )
        url = reverse('resident_invite', args=[raw])

        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(duplicate_one)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(resident)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url)
        self.assertRedirects(response, reverse('resident_account', args=[second.pk]))
        self.assertTrue(ResidentAccess.objects.filter(user=resident, account=second).exists())


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
