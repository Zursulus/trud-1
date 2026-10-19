from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from ._test_auth import login_with_admin_otp
from .access_requests import ResidentAccessRequest
from .access_workflow import approve_access_request, email_access_request_invite, revoke_invite
from .models import Account, ResidentAccess, ResidentInvite, User
from .portal import issue_invite, token_digest


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   DEFAULT_FROM_EMAIL='office@example.test')
class AccessInviteEmailTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', stdout=StringIO())
        cls.actor = User.objects.create_user(username='invite-mail-staff', is_staff=True)
        cls.actor.groups.add(Group.objects.get(name='Администратор ТСН'),
                             Group.objects.get(name='Закрытый реестр членов ТСН'))
        cls.reviewer = User.objects.create_user(username='invite-mail-reviewer', is_staff=True)
        cls.reviewer.groups.add(Group.objects.get(name='Закрытый реестр членов ТСН'))
        cls.account = Account.objects.create(number='MAIL-TEST-1', plot='Тестовый участок')
        cls.request_obj = ResidentAccessRequest.objects.create(
            full_name='Тестовый заявитель', email='request@example.test',
            plot_hint='Тестовый участок', claimed_role='owner', submission_key='invite-mail-test',
        )
        cls.request_obj, cls.old_invite, cls.old_raw = approve_access_request(
            cls.request_obj.pk, account=cls.account, email='verified@example.test',
            role='owner', decision_note='Личность проверена', actor=cls.actor,
        )

    def setUp(self):
        login_with_admin_otp(self.client, self.actor)
        self.url = reverse('staff_workspace:access_request', args=[self.request_obj.pk])
        self.data = {'action': 'email_invite', 'email-reason': '  Адрес проверен лично  ',
                     'email-confirmed': 'on'}

    def assert_unchanged(self):
        self.request_obj.refresh_from_db()
        self.old_invite.refresh_from_db()
        self.assertEqual(self.request_obj.invite_id, self.old_invite.pk)
        self.assertFalse(self.old_invite.revoked)
        self.assertEqual(ResidentInvite.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_form_is_explicit_and_uses_verified_invite_email(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Отправить приглашение на почту')
        self.assertContains(response, 'Получатель: <strong>verified@example.test</strong>', html=True)
        self.assertContains(response, 'name="email-reason"')
        self.assertContains(response, 'name="email-confirmed"')
        self.assertContains(response, 'maxlength="500"')
        self.assertContains(response, 'data-hint="Открыть форму')
        self.assertContains(response, 'aria-hidden="true"')
        self.assertNotContains(response, self.old_raw)
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_fields_do_not_send_or_rotate(self):
        for changes in ({'email-reason': ''}, {'email-reason': ' \n '},
                        {'email-confirmed': ''}, {'email-reason': 'я' * 501}):
            with self.subTest(changes=changes):
                response = self.client.post(self.url, {**self.data, **changes})
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.context['email_form'].errors)
                self.assertContains(response, 'class="ws-invite-email" open')
                self.assert_unchanged()

    def test_missing_authority_is_forbidden_in_view_and_service(self):
        login_with_admin_otp(self.client, self.reviewer)
        self.assertNotContains(self.client.get(self.url), 'Отправить приглашение на почту')
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        with self.assertRaises(PermissionDenied):
            email_access_request_invite(
                self.request_obj.pk, actor=self.reviewer, reason='Проверено', confirmed=True,
                build_absolute_uri=RequestFactory().get('/').build_absolute_uri,
            )
        self.assert_unchanged()

    def test_service_also_validates_reason_and_confirmation(self):
        for reason, confirmed in [(' ', True), ('я' * 501, True), ('Проверено', False)]:
            with self.subTest(reason=reason, confirmed=confirmed), self.assertRaises(ValidationError):
                email_access_request_invite(
                    self.request_obj.pk, actor=self.actor, reason=reason, confirmed=confirmed,
                    build_absolute_uri=RequestFactory().get('/').build_absolute_uri,
                )
            self.assert_unchanged()

    def test_success_sends_one_valid_new_token_and_redirects_without_raw_link(self):
        response = self.client.post(self.url, self.data)
        self.assertRedirects(response, self.url)
        self.request_obj.refresh_from_db()
        self.old_invite.refresh_from_db()
        new_invite = self.request_obj.invite
        self.assertNotEqual(new_invite.pk, self.old_invite.pk)
        self.assertTrue(self.old_invite.revoked)
        self.assertEqual((new_invite.account_id, new_invite.email, new_invite.role),
                         (self.account.pk, 'verified@example.test', 'owner'))
        self.assertFalse(new_invite.revoked)
        self.assertIsNone(new_invite.used_at)
        self.assertAlmostEqual((new_invite.expires_at - timezone.now()).total_seconds(),
                               7 * 86400, delta=20)
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['verified@example.test'])
        self.assertEqual(message.from_email, 'office@example.test')
        self.assertEqual(message.subject, 'Приглашение в личный кабинет ТСН «Труд-1»')
        link = next(line for line in message.body.splitlines() if line.startswith('http://'))
        raw = link.rstrip('/').rsplit('/', 1)[-1]
        self.assertEqual(new_invite.token_hash, token_digest(raw))
        self.assertEqual(Client().get(link).status_code, 200)
        self.assertEqual(Client().get(reverse('resident_invite', args=[self.old_raw])).status_code, 410)
        self.assertIn('7 дней', message.body)
        self.assertIn('Если вы не запрашивали доступ — проигнорируйте', message.body)
        page = self.client.get(self.url)
        self.assertNotContains(page, raw)
        self.assertIsNone(page.context['invite_url'])
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(ResidentAccess.objects.count(), 0)
        self.assertEqual(self.request_obj.decision_note, 'Личность проверена')
        self.assertEqual(self.request_obj.decided_by, self.actor)
        for invite in (self.old_invite, new_invite):
            snapshot = invite.history.first()
            self.assertEqual(snapshot.history_user_id, self.actor.pk)
            self.assertIn('Адрес проверен лично', snapshot.history_change_reason)
            self.assertNotIn(raw, snapshot.history_change_reason)
        for obj in (self.request_obj, self.old_invite, new_invite):
            log = LogEntry.objects.get(content_type=ContentType.objects.get_for_model(obj),
                                       object_id=str(obj.pk))
            self.assertEqual(log.user_id, self.actor.pk)
            self.assertIn('Основание: Адрес проверен лично.', log.change_message)
            self.assertNotIn(raw, log.change_message)

    def test_long_reason_fits_existing_history_columns_and_is_preserved_in_admin_history(self):
        reason = 'Основание ' + 'я' * 490
        response = self.client.post(self.url, {**self.data, 'email-reason': reason})
        self.assertEqual(response.status_code, 302)
        self.request_obj.refresh_from_db()
        self.assertLessEqual(len(self.request_obj.invite.history.first().history_change_reason), 100)
        for log in LogEntry.objects.all():
            self.assertIn(reason, log.change_message)

    def test_mailed_invite_activates_resident_access_and_cannot_be_sent_afterwards(self):
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        link = next(line for line in mail.outbox[0].body.splitlines() if line.startswith('http://'))
        resident_client = Client(enforce_csrf_checks=True)
        resident_client.get(link)
        activated = resident_client.post(link, {
            'password1': 'Synthetic-resident-pass-617!',
            'password2': 'Synthetic-resident-pass-617!',
            'csrfmiddlewaretoken': resident_client.cookies['csrftoken'].value,
        })
        self.assertRedirects(activated, reverse('resident_dashboard'))
        access = ResidentAccess.objects.get(account=self.account)
        self.assertEqual(access.user.email, self.old_invite.email)
        self.assertEqual(access.role, 'owner')
        self.request_obj.refresh_from_db()
        self.assertIsNotNone(self.request_obj.invite.used_at)
        self.assertNotContains(self.client.get(self.url), 'Отправить приглашение на почту')
        self.assertContains(self.client.post(self.url, self.data), 'Приглашение уже использовано жителем')
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(ResidentInvite.objects.count(), 2)

    def test_used_invite_has_explanation_and_post_is_rejected(self):
        self.old_invite.used_at = timezone.now()
        self.old_invite.save()
        page = self.client.get(self.url)
        self.assertNotContains(page, 'Отправить приглашение на почту')
        self.assertContains(page, 'Приглашение уже использовано жителем')
        response = self.client.post(self.url, self.data)
        self.assertContains(response, 'Приглашение уже использовано жителем')
        self.assert_unchanged()

    def test_revoked_and_replaced_invites_cannot_be_reissued_from_stale_request(self):
        revoke_invite(self.old_invite.pk, actor=self.actor)
        self.assertNotContains(self.client.get(self.url), 'Отправить приглашение на почту')
        response = self.client.post(self.url, self.data)
        self.assertContains(response, 'Приглашение отозвано или заменено')
        self.assertEqual(ResidentInvite.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 0)
        # In particular, a replacement activated outside this request must not
        # allow the stale approved request to reopen that resident's access.
        replacement, _ = issue_invite(self.account, self.old_invite.email, 'owner', actor=self.actor)
        replacement.used_at = timezone.now()
        replacement.save()
        self.client.post(self.url, self.data)
        self.assertEqual(ResidentInvite.objects.count(), 2)
        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.invite_id, self.old_invite.pk)
        self.assertEqual(len(mail.outbox), 0)

    def test_expired_unused_invite_can_be_sent_with_a_fresh_seven_day_term(self):
        self.old_invite.expires_at = timezone.now() - timedelta(days=1)
        self.old_invite.save()
        self.assertContains(self.client.get(self.url), 'Отправить приглашение на почту')
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    def test_mail_failure_rolls_back_invites_request_and_audit(self):
        before_history = self.old_invite.history.count()
        for result in (RuntimeError('SMTP secret details'), 0):
            with self.subTest(result=result):
                options = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch('water.access_workflow.send_mail', **options):
                    response = self.client.post(self.url, self.data)
                self.assertContains(response, 'Письмо не отправлено: почта сайта недоступна')
                self.assertNotContains(response, 'SMTP secret details')
                self.assert_unchanged()
                self.assertEqual(self.old_invite.history.count(), before_history)
                self.assertEqual(LogEntry.objects.count(), 0)

    def test_csrf_is_required_and_valid_token_allows_send(self):
        client = Client(enforce_csrf_checks=True)
        login_with_admin_otp(client, self.actor)
        page = client.get(self.url)
        self.assertContains(page, 'name="csrfmiddlewaretoken"')
        self.assertEqual(client.post(self.url, self.data).status_code, 403)
        self.assert_unchanged()
        response = client.post(self.url, {**self.data, 'csrfmiddlewaretoken': client.cookies['csrftoken'].value})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

    def test_unapproved_request_and_arbitrary_invite_reassignment_remain_forbidden(self):
        pending = ResidentAccessRequest.objects.create(
            full_name='Тестовый заявитель', email='pending@example.test', plot_hint='Тестовый участок',
            claimed_role='owner', submission_key='invite-mail-pending',
        )
        pending_url = reverse('staff_workspace:access_request', args=[pending.pk])
        self.assertNotContains(self.client.get(pending_url), 'Отправить приглашение на почту')
        self.assertContains(self.client.post(pending_url, self.data), 'только для одобренной заявки')
        self.assert_unchanged()
        replacement, _ = issue_invite(self.account, self.old_invite.email, 'owner', actor=self.actor)
        self.request_obj.invite = replacement
        with self.assertRaisesMessage(ValidationError, 'решение по заявке нельзя переписывать'):
            self.request_obj.save()
