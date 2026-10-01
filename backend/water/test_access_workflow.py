from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .access_requests import ResidentAccessRequest
from .access_workflow import (
    approve_access_request,
    end_resident_access,
    issue_access_password_reset,
    reject_access_request,
    revoke_invite,
    revoke_password_reset,
)
from .models import Account, ResidentAccess, ResidentInvite, ResidentPasswordReset, User


ADMIN = 'Администратор ТСН'
PRIVATE = 'Закрытый реестр членов ТСН'
STANDARD_GROUPS = {ADMIN, 'Оператор воды', 'Контролёр воды', PRIVATE}


class AccessRoleBoundaryTests(TestCase):
    def setUp(self):
        call_command('setup_roles', stdout=StringIO())

    def test_existing_four_role_model_is_preserved(self):
        names = set(Group.objects.values_list('name', flat=True))
        self.assertTrue(STANDARD_GROUPS.issubset(names))
        self.assertNotIn('Доступ жителей', names)

    def test_private_registry_gets_request_review_but_not_access_management(self):
        codes = set(Group.objects.get(name=PRIVATE).permissions.values_list('codename', flat=True))
        self.assertIn('access_private_registry', codes)
        self.assertIn('view_residentaccessrequest', codes)
        self.assertIn('change_residentaccessrequest', codes)
        self.assertNotIn('add_residentaccessrequest', codes)
        self.assertNotIn('add_residentinvite', codes)
        self.assertNotIn('change_residentaccess', codes)
        self.assertNotIn('add_residentpasswordreset', codes)
        self.assertNotIn('change_payment', codes)
        self.assertNotIn('add_reading', codes)

    def test_regular_admin_manages_access_without_request_pii(self):
        codes = set(Group.objects.get(name=ADMIN).permissions.values_list('codename', flat=True))
        self.assertIn('add_residentinvite', codes)
        self.assertIn('change_residentaccess', codes)
        self.assertIn('add_residentpasswordreset', codes)
        self.assertNotIn('access_private_registry', codes)
        self.assertNotIn('view_residentaccessrequest', codes)
        self.assertNotIn('change_residentaccessrequest', codes)


class AccessWorkflowTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(username='access-actor', password='test', is_staff=True)
        call_command('setup_roles', stdout=StringIO())
        self.actor.groups.add(Group.objects.get(name=ADMIN), Group.objects.get(name=PRIVATE))
        self.account = Account.objects.create(number='ACCESS-1', plot='Лесная 1')
        self.request = ResidentAccessRequest.objects.create(
            full_name='Тестовый Заявитель',
            email='resident@example.test',
            phone='+7 900 000-00-01',
            plot_hint='Лесная 1',
            claimed_role=ResidentAccessRequest.CLAIM_OWNER,
            message='Проверка workflow',
            submission_key='workflow-request-key',
        )

    def test_approval_is_atomic_and_creates_invite_not_access(self):
        decided, invite, raw = approve_access_request(
            self.request.pk,
            account=self.account,
            email=self.request.email,
            role='owner',
            decision_note='Основание проверено',
            actor=self.actor,
        )
        self.assertEqual(decided.status, ResidentAccessRequest.STATUS_APPROVED)
        self.assertEqual(decided.invite_id, invite.pk)
        self.assertTrue(raw)
        self.assertEqual(ResidentInvite.objects.count(), 1)
        self.assertEqual(ResidentAccess.objects.count(), 0)

        with self.assertRaisesMessage(ValidationError, 'решение уже принято'):
            approve_access_request(
                self.request.pk,
                account=self.account,
                email=self.request.email,
                role='owner',
                decision_note='Повтор',
                actor=self.actor,
            )
        self.assertEqual(ResidentInvite.objects.count(), 1)

    def test_approval_rejects_ambiguous_active_email_before_creating_invite(self):
        User.objects.create_user(username='duplicate-one', email=self.request.email, password='test')
        User.objects.create_user(username='duplicate-two', email=self.request.email, password='test')

        with self.assertRaisesMessage(ValidationError, 'несколькими активными учётными записями'):
            approve_access_request(
                self.request.pk,
                account=self.account,
                email=self.request.email,
                role='payer',
                decision_note='Основание проверено',
                actor=self.actor,
            )

        self.request.refresh_from_db()
        self.assertEqual(self.request.status, ResidentAccessRequest.STATUS_NEW)
        self.assertEqual(ResidentInvite.objects.count(), 0)

    def test_rejection_requires_reason_and_is_immutable(self):
        with self.assertRaisesMessage(ValidationError, 'Причина отклонения: обязательно заполнить'):
            reject_access_request(self.request.pk, decision_note='  ', actor=self.actor)
        decided = reject_access_request(
            self.request.pk, decision_note='Основание не подтверждено', actor=self.actor,
        )
        self.assertEqual(decided.status, ResidentAccessRequest.STATUS_REJECTED)
        self.assertEqual(ResidentInvite.objects.count(), 0)
        with self.assertRaisesMessage(ValidationError, 'решение уже принято'):
            reject_access_request(self.request.pk, decision_note='Повтор', actor=self.actor)

    def test_unused_invite_can_be_revoked_only_once(self):
        _, invite, _ = approve_access_request(
            self.request.pk,
            account=self.account,
            email=self.request.email,
            role='owner',
            decision_note='Основание проверено',
            actor=self.actor,
        )
        revoked = revoke_invite(invite.pk, actor=self.actor)
        self.assertTrue(revoked.revoked)
        with self.assertRaisesMessage(ValidationError, 'уже отозвано'):
            revoke_invite(invite.pk, actor=self.actor)

    def test_access_is_ended_by_date_not_deleted(self):
        resident = User.objects.create_user(username='resident', password='test', email='resident2@example.test')
        starts = timezone.localdate() - timedelta(days=10)
        access = ResidentAccess.objects.create(
            user=resident, account=self.account, role='owner', starts=starts,
        )
        ended = end_resident_access(access.pk, ends_on=timezone.localdate(), actor=self.actor)
        self.assertEqual(ended.ends, timezone.localdate())
        self.assertTrue(ResidentAccess.objects.filter(pk=access.pk).exists())
        with self.assertRaisesMessage(ValidationError, 'уже завершён'):
            end_resident_access(access.pk, ends_on=timezone.localdate(), actor=self.actor)

    def test_password_reset_keeps_existing_token_rules_and_can_be_revoked(self):
        resident = User.objects.create_user(username='resident-reset', password='test', email='reset@example.test')
        access = ResidentAccess.objects.create(
            user=resident,
            account=self.account,
            role='owner',
            starts=timezone.localdate() - timedelta(days=1),
        )
        reset, raw = issue_access_password_reset(access.pk, actor=self.actor)
        self.assertTrue(raw)
        self.assertEqual(ResidentPasswordReset.objects.count(), 1)
        revoked = revoke_password_reset(reset.pk, actor=self.actor)
        self.assertTrue(revoked.revoked)
        with self.assertRaisesMessage(ValidationError, 'уже отозвана'):
            revoke_password_reset(reset.pk, actor=self.actor)
