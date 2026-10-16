from unittest.mock import patch

from allauth.account.models import EmailAddress
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from public_site.legal import LegalDocument, current_document
from water.access_requests import ResidentAccessRequest
from water.access_workflow import approve_binding_request
from water.models import Account, Person, User
from water.portal_permissions import PortalGrant
from water.private_registry import MemberRegistryEntry
from water.resident_models import ResidentIdentity
from water.resident_numbers import ResidentNumberSlot
from .models import ConsentRecord, DeletionRequest, Profile
from .test_helpers import consent_payload, record_consents


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   RESIDENT_EMAIL_LOGIN_ENABLED=True, FORUM_ENABLED=True)
class LegalConsentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('legal-resident', email='legal@example.test')

    def signup(self, **changes):
        payload = {'email': 'signup@example.test', **consent_payload(), **changes}
        return self.client.post(reverse('account_signup'), payload,
                                REMOTE_ADDR='198.51.100.1', HTTP_USER_AGENT='Legal test')

    def test_registration_requires_both_separate_unchecked_boxes(self):
        form = self.client.get(reverse('account_signup')).context['form']
        self.assertFalse(form['consent'].value())
        self.assertFalse(form['rules'].value())
        self.assertIn(reverse('legal_document', args=['consent']), str(form['consent'].label))
        for omitted in ('consent', 'rules'):
            response = self.signup(**{omitted: ''})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(User.objects.filter(email='signup@example.test').exists())
        self.assertFalse(ConsentRecord.objects.exists())

    def test_registration_records_exact_documents_and_request_metadata(self):
        self.assertEqual(self.signup().status_code, 302)
        user = User.objects.get(email='signup@example.test')
        self.assertEqual(user.consent_records.count(), 2)
        for record in user.consent_records.all():
            self.assertEqual(record.document, current_document(record.document_type))
            self.assertEqual(record.version, record.document.version)
            self.assertEqual(record.ip, '198.51.100.1')
            self.assertEqual(record.user_agent, 'Legal test')
            self.assertEqual(record.action, 'accept')
            self.assertIsNotNone(record.created_at)

    def test_document_changed_after_display_requires_fresh_acceptance(self):
        payload = consent_payload()
        old = current_document('consent')
        LegalDocument.objects.create(kind='consent', version='new-test', effective_on=timezone.localdate(),
                                     body='Новая тестовая версия')
        response = self.client.post(reverse('account_signup'), {'email': 'signup@example.test', **payload})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Документы обновились')
        self.assertFalse(User.objects.filter(email='signup@example.test').exists())
        archived = self.client.get(reverse('legal_document', args=['consent']) + '?version=' + old.version)
        self.assertContains(archived, old.version)

    def test_journal_and_document_cannot_be_rewritten_or_deleted(self):
        record_consents(self.user)
        obj = self.user.consent_records.first()
        for action in (lambda: obj.save(), lambda: obj.delete(),
                       lambda: ConsentRecord.objects.filter(pk=obj.pk).update(action='withdraw'),
                       lambda: ConsentRecord.objects.filter(pk=obj.pk).delete(),
                       lambda: ConsentRecord.objects.bulk_update([obj], ['action']),
                       lambda: ConsentRecord.objects.bulk_create([obj], update_conflicts=True,
                           update_fields=['action'], unique_fields=['id']),
                       lambda: obj.document.save(), lambda: obj.document.delete()):
            with self.assertRaises(ValidationError):
                action()

    def test_old_resident_accepts_once_after_actual_email_login(self):
        with patch('allauth.account.adapter.DefaultAccountAdapter.generate_login_code', return_value='123456'):
            self.client.post(reverse('account_login'), {'email': self.user.email})
        result = self.client.post(reverse('account_confirm_login_code'), {'code': '123456'})
        self.assertRedirects(result, reverse('resident_legal_accept'))
        self.assertRedirects(self.client.get(reverse('forum:index')), reverse('resident_legal_accept'))
        self.assertRedirects(self.client.get(reverse('resident_dashboard')), reverse('resident_legal_accept'))
        result = self.client.post(reverse('resident_legal_accept'), consent_payload())
        self.assertRedirects(result, reverse('forum:index'))
        self.assertEqual(self.user.consent_records.count(), 2)
        self.assertEqual(self.client.get(reverse('forum:index')).status_code, 200)
        self.client.post(reverse('resident_legal_accept'), consent_payload())
        self.assertEqual(self.user.consent_records.count(), 2)
        self.client.post(reverse('resident_logout'))
        with patch('allauth.account.adapter.DefaultAccountAdapter.generate_login_code', return_value='123456'):
            self.client.post(reverse('account_login'), {'email': self.user.email})
        self.assertRedirects(self.client.post(reverse('account_confirm_login_code'), {'code': '123456'}),
                             reverse('forum:index'))

    def test_old_password_resident_also_sees_checkpoint(self):
        self.user.set_password('test-password')
        self.user.save()
        # Password resident authentication requires existing portal access.
        from water.models import ResidentAccess
        account = Account.objects.create(number='LEGAL-LOGIN', plot='Тест')
        ResidentAccess.objects.create(user=self.user, account=account, role='payer', starts=timezone.localdate())
        result = self.client.post(reverse('resident_login'), {'username': self.user.username, 'password': 'test-password'})
        self.assertRedirects(result, reverse('resident_legal_accept'))

    def test_withdrawal_revokes_both_consents_and_all_live_sessions(self):
        record_consents(self.user, ('consent', 'rules', 'binding'))
        EmailAddress.objects.create(user=self.user, email=self.user.email, verified=True, primary=True)
        self.client.force_login(self.user)
        second = Client()
        second.force_login(self.user, backend='forum.auth.EmailCodeBackend')
        self.assertContains(self.client.get(reverse('resident_legal_profile')), 'Отозвать согласие')
        result = self.client.post(reverse('resident_legal_profile'), {'action': 'withdraw'})
        self.assertContains(result, 'Согласие отозвано')
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(DeletionRequest.objects.get().user, self.user)
        self.assertEqual(self.user.consent_records.filter(action='withdraw').count(), 2)
        self.assertEqual(second.get(reverse('forum:index')).status_code, 302)
        self.client.post(reverse('account_login'), {'email': self.user.email})
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_csrf_protects_acceptance_and_withdrawal(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        client.get(reverse('resident_legal_accept'))
        self.assertFalse(client.cookies['csrftoken']['max-age'])
        self.assertFalse(client.cookies['csrftoken']['expires'])
        for url, payload in ((reverse('resident_legal_accept'), consent_payload()),
                             (reverse('resident_legal_profile'), {'action': 'withdraw'})):
            self.assertEqual(client.post(url, payload).status_code, 403)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertFalse(DeletionRequest.objects.exists())

    def test_public_documents_and_footer_links(self):
        for kind, title in LegalDocument.KINDS:
            response = self.client.get(reverse('legal_document', args=[kind]))
            self.assertContains(response, title)
            self.assertContains(response, 'Черновик — на согласование юристу')
            self.assertContains(response, '2026-10-16-draft-1')
        self.assertEqual(self.client.get(reverse('legal_document', args=['unknown'])).status_code, 404)
        self.client.force_login(self.user)
        response = self.client.get(reverse('forum:index'))
        self.assertContains(response, 'Не пишите участок и показания на форуме')
        self.assertContains(response, reverse('resident_access_request'))
        for kind, title in LegalDocument.KINDS:
            self.assertContains(response, reverse('legal_document', args=[kind]))
            self.assertContains(Client().get(reverse('resident_login')), reverse('legal_document', args=[kind]))


@override_settings(FORUM_ENABLED=True, RESIDENT_EMAIL_LOGIN_ENABLED=True)
class BindingReviewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', verbosity=0)
        cls.board = User.objects.create_user('legal-board', is_staff=True)
        cls.board.groups.add(Group.objects.get(name='Администратор ТСН'),
                             Group.objects.get(name='Закрытый реестр членов ТСН'))
        cls.operator = User.objects.create_user('legal-operator', is_staff=True)
        cls.operator.groups.add(Group.objects.get(name='Оператор воды'))
        cls.account = Account.objects.create(number='LEGAL-1', plot='Тестовый участок')
        cls.person = Person.objects.create(full_name='Тестовый Житель')
        cls.user = User.objects.create_user('binding-resident', email='binding@example.test')
        record_consents(cls.user)

    def setUp(self):
        self.client.force_login(self.user)
        payload = {'full_name': 'Тестовый Житель', 'plot_hint': 'Тестовый участок', 'phone': '',
                   'claimed_role': 'owner', 'last_reading': '100.001', **consent_payload(binding=True)}
        self.assertEqual(self.client.post(reverse('resident_access_request'), payload).status_code, 302)
        self.request = ResidentAccessRequest.objects.get()

    def approve(self, **changes):
        params = dict(person=self.person, account=self.account, basis='Сверены ФИО, участок и полномочия',
                      checked=True, actor=self.board, can_view_account=True, can_view_finance=False,
                      can_submit_water=True, can_view_documents=False, can_use_appeals=True,
                      can_represent=False)
        params.update(changes)
        return approve_binding_request(self.request.pk, **params)

    def test_application_visible_only_to_private_board(self):
        url = reverse('staff_workspace:access_request', args=[self.request.pk])
        for user in (self.user, self.operator):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertIn(response.status_code, (302, 403))
            self.assertNotContains(response, '100,001', status_code=response.status_code)
        self.client.force_login(self.board)
        response = self.client.get(url)
        self.assertContains(response, self.request.full_name)
        self.assertContains(response, 'Сверено — выдать доступ')
        self.assertContains(response, '100,001')

    def test_manual_check_required_and_grant_created_once_with_history(self):
        with self.assertRaises(ValidationError):
            self.approve(checked=False)
        self.assertFalse(PortalGrant.objects.exists())
        self.approve()
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, 'approved')
        self.assertEqual(self.request.grant.account, self.account)
        self.assertTrue(self.request.grant.can_submit_water)
        self.assertFalse(self.request.grant.can_view_finance)
        self.assertFalse(self.request.invite_id)
        self.assertEqual(ResidentIdentity.objects.get(user=self.user).person, self.person)
        self.assertEqual(self.request.history.count(), 2)
        self.assertTrue(self.request.grant.history.exists())
        with self.assertRaises(ValidationError):
            self.approve()
        self.assertEqual(PortalGrant.objects.count(), 1)
        self.assertContains(self.client.get(reverse('resident_legal_profile')), 'Одобрена')

    def test_workspace_button_uses_same_approval_service(self):
        self.client.force_login(self.board)
        response = self.client.post(reverse('staff_workspace:access_request', args=[self.request.pk]), {
            'action': 'approve', 'approve-person': self.person.pk, 'approve-account': self.account.pk,
            'approve-basis': 'Ручная сверка по реестру', 'approve-checked': 'on',
            'approve-can_view_account': 'on', 'approve-can_submit_water': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(PortalGrant.objects.count(), 1)

    def test_operator_cannot_grant_and_withdrawn_resident_cannot_receive_grant(self):
        with self.assertRaises(PermissionDenied):
            self.approve(actor=self.operator)
        self.client.post(reverse('resident_legal_profile'), {'action': 'withdraw'})
        with self.assertRaises(ValidationError):
            self.approve()
        self.assertFalse(PortalGrant.objects.exists())

    def test_existing_identity_is_not_silently_reassigned(self):
        other = Person.objects.create(full_name='Другой человек')
        ResidentIdentity.objects.create(user=self.user, person=other, verified_by=self.board)
        with self.assertRaises(ValidationError):
            self.approve()
        self.assertFalse(PortalGrant.objects.exists())

    def test_registry_email_mark_requires_exact_member_record_and_is_invalid_after_change(self):
        with self.assertRaises(ValidationError):
            self.approve(registry_email_checked=True)
        self.assertFalse(PortalGrant.objects.exists())
        slot = ResidentNumberSlot.objects.get(number=1)
        MemberRegistryEntry.objects.create(resident_number=slot, person=self.person,
                                           account=self.account, email=self.user.email)
        self.approve(registry_email_checked=True)
        profile = Profile.objects.get(user=self.user)
        self.assertTrue(profile.registry_email_matches)
        self.assertEqual(profile.email_verified_by, self.board)
        self.assertTrue(profile.history.exists())
        self.assertContains(self.client.get(reverse('resident_legal_profile')), 'сверено правлением')
        cabinet_profile = self.client.get(reverse('resident_profile', args=[self.account.pk]))
        self.assertContains(cabinet_profile, 'сверено правлением')
        self.assertContains(cabinet_profile, 'Отозвать согласие')
        self.user.email = 'changed@example.test'
        self.user.save()
        profile.refresh_from_db()
        self.assertFalse(profile.registry_email_matches)

    def test_deletion_queue_does_not_leak_to_water_staff(self):
        self.client.post(reverse('resident_legal_profile'), {'action': 'withdraw'})
        self.client.force_login(self.board)
        response = self.client.get(reverse('staff_workspace:access'))
        self.assertContains(response, 'Заявки на удаление ПД')
        self.assertContains(response, self.user.email)
        self.client.force_login(self.operator)
        self.assertEqual(self.client.get(reverse('admin:forum_consentrecord_changelist')).status_code, 403)
        item = DeletionRequest.objects.get()
        self.assertEqual(self.client.get(reverse('admin:forum_deletionrequest_change', args=[item.pk])).status_code, 403)
        self.client.force_login(self.board)
        self.assertEqual(self.client.get(reverse('admin:forum_deletionrequest_change', args=[item.pk])).status_code, 200)
