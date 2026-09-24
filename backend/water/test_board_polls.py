from datetime import date, timedelta
import tempfile

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from .board_polls import (
    BoardAuditEvent,
    BoardDiscussionComment,
    BoardMembership,
    BoardPoll,
    BoardProtocol,
    BoardQuestion,
    BoardVote,
    pending_board_poll_count,
)
from .models import Account, ResidentAccess, User


class BoardPollTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media.cleanup)

        self.staff = User.objects.create_user(username='board-admin', password='x', is_staff=True)
        self.member = User.objects.create_user(username='board-member', first_name='Анна', password='x')
        self.member2 = User.objects.create_user(username='board-member-2', first_name='Борис', password='x')
        self.outsider = User.objects.create_user(username='board-outsider', password='x')
        self.account = Account.objects.create(number='BOARD-1', plot='Тестовый участок правления')
        ResidentAccess.objects.create(
            user=self.member, account=self.account, role='owner', starts=date(2026, 1, 1),
        )
        BoardMembership.objects.create(user=self.member, role='member', starts=date(2026, 1, 1))
        BoardMembership.objects.create(user=self.member2, role='member', starts=date(2026, 1, 1))
        now = timezone.now()
        self.poll = BoardPoll.objects.create(
            title='Проверочный вопрос', description='Только предварительное обсуждение',
            opens_at=now - timedelta(hours=1), closes_at=now + timedelta(days=1),
            created_by=self.staff,
        )
        self.question = BoardQuestion.objects.create(poll=self.poll, order=1, text='Поддержать рабочий вариант?')

    def _vote(self, choice='for', comment=''):
        self.client.force_login(self.member)
        return self.client.post(
            f'/admin/cabinet/board/poll/{self.poll.pk}/',
            {'action': 'vote', 'question_id': self.question.pk, 'choice': choice, 'comment': comment},
        )

    def test_only_board_member_or_staff_can_open_board_workspace(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get('/admin/cabinet/board/').status_code, 403)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get('/admin/cabinet/board/').status_code, 200)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/admin/cabinet/board/').status_code, 200)

    def test_vote_is_one_row_and_changes_are_audited(self):
        first = self._vote('for', 'Первый вариант')
        self.assertEqual(first.status_code, 302)
        second = self._vote('against', 'Изменил мнение')
        self.assertEqual(second.status_code, 302)
        votes = BoardVote.objects.filter(question=self.question, user=self.member)
        self.assertEqual(votes.count(), 1)
        self.assertEqual(votes.get().choice, 'against')
        events = BoardAuditEvent.objects.filter(target_type='boardvote', target_id=votes.get().pk)
        self.assertEqual(events.count(), 2)
        self.assertEqual(list(events.order_by('created_at').values_list('action', flat=True)), ['created', 'changed'])

    def test_closed_poll_rejects_vote_change(self):
        self._vote('for')
        self.poll.refresh_from_db()
        self.poll.closed_at = timezone.now()
        self.poll._audit_actor = self.staff
        self.poll._audit_reason = 'Закрытие тестового опроса'
        self.poll.save()
        response = self._vote('against')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(BoardVote.objects.get(question=self.question, user=self.member).choice, 'for')

    def test_question_with_vote_cannot_be_rewritten(self):
        self._vote('for')
        self.question.refresh_from_db()
        self.question.text = 'Другой смысл вопроса'
        with self.assertRaises(ValidationError):
            self.question.save()

    def test_discussion_comment_is_immutable(self):
        comment = BoardDiscussionComment.objects.create(
            question=self.question, author=self.member, body='Аргумент по вопросу',
        )
        comment.body = 'Переписанный аргумент'
        with self.assertRaises(ValidationError):
            comment.save()

    def test_results_show_voters_and_non_voters(self):
        self._vote('for')
        response = self.client.get(f'/admin/cabinet/board/poll/{self.poll.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Анна')
        self.assertContains(response, 'Борис')
        self.assertContains(response, 'Ещё не ответили')
        self.assertContains(response, 'Неофициальный контур')

    def test_protocol_is_private_and_available_after_poll(self):
        self.poll.closed_at = timezone.now()
        self.poll._audit_actor = self.staff
        self.poll._audit_reason = 'Закрытие для протокола'
        self.poll.save()
        protocol = BoardProtocol.objects.create(
            poll=self.poll,
            document=SimpleUploadedFile('protocol.pdf', b'%PDF-1.4\nboard-test', content_type='application/pdf'),
            uploaded_by=self.staff,
        )
        self.client.force_login(self.member)
        response = self.client.get(f'/admin/cabinet/board/poll/{self.poll.pk}/protocol/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertEqual(protocol.original_name, 'protocol.pdf')
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(f'/admin/cabinet/board/poll/{self.poll.pk}/protocol/').status_code, 403)

    def test_dashboard_notifies_about_pending_board_poll(self):
        self.client.force_login(self.member)
        self.assertEqual(pending_board_poll_count(self.member), 1)
        response = self.client.get(f'/admin/cabinet/account/{self.account.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Правление: ждёт ваш голос')
        self._vote('for')
        self.assertEqual(pending_board_poll_count(self.member), 0)

    def test_poll_schema_has_no_legal_quorum_or_signature_fields(self):
        names = {field.name for field in BoardPoll._meta.fields}
        self.assertTrue({'title', 'opens_at', 'closes_at', 'closed_at'} <= names)
        self.assertFalse({'quorum', 'signature', 'legal_result'} & names)
