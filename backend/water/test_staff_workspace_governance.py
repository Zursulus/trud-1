from datetime import date, timedelta
from tempfile import TemporaryDirectory

from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from .board_polls import BoardAuditEvent, BoardMembership, BoardPoll, BoardProtocol, BoardQuestion, BoardVote
from .models import User


class StaffWorkspaceGovernanceTests(TestCase):
    def setUp(self):
        self._media = TemporaryDirectory()
        self._media_override = override_settings(MEDIA_ROOT=self._media.name)
        self._media_override.enable()
        self.addCleanup(self._media_override.disable)
        self.addCleanup(self._media.cleanup)

        self.admin = User.objects.create_superuser(username="governance-admin", password="x", email="admin@example.invalid")
        self.viewer = User.objects.create_user(username="governance-viewer", password="x", is_staff=True)
        self.viewer.user_permissions.add(Permission.objects.get(codename="view_boardpoll", content_type__app_label="water"))
        self.no_permission = User.objects.create_user(username="governance-none", password="x", is_staff=True)
        self.member = User.objects.create_user(username="governance-member", password="x", first_name="Анна")
        self.member2 = User.objects.create_user(username="governance-member2", password="x", first_name="Борис")
        BoardMembership.objects.create(user=self.member, starts=date(2026, 1, 1))
        BoardMembership.objects.create(user=self.member2, starts=date(2026, 1, 1))
        self.poll = BoardPoll.objects.create(
            title="Тест предварительного опроса",
            description="Неофициальный контур",
            opens_at=timezone.now() - timedelta(minutes=10),
            closes_at=timezone.now() + timedelta(days=1),
            created_by=self.admin,
        )
        self.question = BoardQuestion.objects.create(poll=self.poll, order=1, text="Поддержать рабочий вариант?")

    def login(self, user):
        self.client.force_login(user)

    def test_view_permission_controls_workspace_and_more_card(self):
        self.login(self.no_permission)
        self.assertEqual(self.client.get("/work/governance/").status_code, 403)
        self.assertNotContains(self.client.get("/work/more/"), "Опросы / правление")

        self.login(self.viewer)
        response = self.client.get("/work/governance/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "предварительный")
        self.assertContains(response, "неофициаль")
        self.assertContains(self.client.get("/work/more/"), "Опросы / правление")

    def test_view_only_staff_cannot_create_edit_or_close_by_direct_post(self):
        self.login(self.viewer)
        self.assertEqual(self.client.get("/work/governance/new/").status_code, 403)
        edit = self.client.post(
            f"/work/governance/{self.poll.pk}/",
            {
                "action": "edit",
                "title": "Попытка изменения",
                "description": self.poll.description,
                "closes_at": timezone.localtime(self.poll.closes_at).strftime("%Y-%m-%dT%H:%M"),
                "version": self.poll.version,
            },
        )
        self.assertEqual(edit.status_code, 403)
        response = self.client.post(
            f"/work/governance/{self.poll.pk}/",
            {"action": "close", "version": self.poll.version},
        )
        self.assertEqual(response.status_code, 403)
        self.poll.refresh_from_db()
        self.assertEqual(self.poll.title, "Тест предварительного опроса")
        self.assertIsNone(self.poll.closed_at)

    def test_create_poll_with_ordered_questions_is_audited(self):
        self.login(self.admin)
        closes_at = timezone.localtime(timezone.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(
            "/work/governance/new/",
            {
                "title": "Рабочий опрос",
                "description": "Только для подготовки позиции",
                "closes_at": closes_at,
                "questions": "Первый вопрос?\nВторой вопрос?",
            },
        )
        self.assertEqual(response.status_code, 302)
        poll = BoardPoll.objects.get(title="Рабочий опрос")
        self.assertEqual(poll.created_by, self.admin)
        self.assertEqual(list(poll.questions.values_list("order", "text")), [(1, "Первый вопрос?"), (2, "Второй вопрос?")])
        self.assertTrue(BoardAuditEvent.objects.filter(target_type="boardpoll", target_id=poll.pk, actor=self.admin).exists())
        self.assertEqual(BoardAuditEvent.objects.filter(target_type="boardquestion", target_id__in=poll.questions.values("pk")).count(), 2)

    def test_open_poll_metadata_edit_is_versioned_audited_and_leaves_questions_untouched(self):
        self.login(self.admin)
        original_question = self.question.text
        closes_at = timezone.localtime(timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
        response = self.client.post(
            f"/work/governance/{self.poll.pk}/",
            {
                "action": "edit",
                "title": "Уточнённый рабочий опрос",
                "description": "Уточнено только пояснение",
                "closes_at": closes_at,
                "version": self.poll.version,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.poll.refresh_from_db()
        self.question.refresh_from_db()
        self.assertEqual(self.poll.title, "Уточнённый рабочий опрос")
        self.assertEqual(self.question.text, original_question)
        self.assertTrue(
            BoardAuditEvent.objects.filter(
                target_type="boardpoll",
                target_id=self.poll.pk,
                summary__icontains="метаданных",
            ).exists()
        )

    def test_detail_shows_results_non_voters_audit_and_legal_boundary(self):
        vote = BoardVote(question=self.question, user=self.member, choice="for", comment="За рабочий вариант")
        vote._audit_actor = self.member
        vote._audit_reason = "Первичный голос"
        vote.save()
        self.login(self.admin)
        response = self.client.get(f"/work/governance/{self.poll.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Анна")
        self.assertContains(response, "Борис")
        self.assertContains(response, "Ещё не ответили")
        self.assertContains(response, "Неизменяемая история")
        self.assertContains(response, "не является общим собранием")
        self.assertContains(response, "вопросы здесь не редактируются")

    def test_close_uses_submitted_version_and_rejects_stale_page(self):
        stale_version = self.poll.version
        self.poll.description = "Изменено параллельно"
        self.poll._audit_actor = self.admin
        self.poll._audit_reason = "Параллельное изменение"
        self.poll.save()
        self.login(self.admin)
        response = self.client.post(
            f"/work/governance/{self.poll.pk}/",
            {"action": "close", "version": stale_version},
        )
        self.assertEqual(response.status_code, 302)
        self.poll.refresh_from_db()
        self.assertIsNone(self.poll.closed_at)

        response = self.client.post(
            f"/work/governance/{self.poll.pk}/",
            {"action": "close", "version": self.poll.version},
        )
        self.assertEqual(response.status_code, 302)
        self.poll.refresh_from_db()
        self.assertIsNotNone(self.poll.closed_at)
        self.assertTrue(
            BoardAuditEvent.objects.filter(
                target_type="boardpoll",
                target_id=self.poll.pk,
                summary__icontains="закрытие",
            ).exists()
        )

    def test_closed_poll_accepts_one_private_immutable_protocol(self):
        self.poll.closed_at = timezone.now()
        self.poll._audit_actor = self.admin
        self.poll._audit_reason = "Закрытие для протокола"
        self.poll.save()
        self.login(self.admin)
        response = self.client.post(
            f"/work/governance/{self.poll.pk}/",
            {
                "action": "upload_protocol",
                "document": SimpleUploadedFile("board.pdf", b"%PDF-1.4 test", content_type="application/pdf"),
            },
        )
        self.assertEqual(response.status_code, 302)
        protocol = BoardProtocol.objects.get(poll=self.poll)
        self.assertEqual(protocol.uploaded_by, self.admin)
        self.assertEqual(protocol.original_name, "board.pdf")
        detail = self.client.get(f"/work/governance/{self.poll.pk}/")
        self.assertContains(detail, "Скачать протокол")

        response = self.client.post(
            f"/work/governance/{self.poll.pk}/",
            {
                "action": "upload_protocol",
                "document": SimpleUploadedFile("replacement.pdf", b"%PDF-1.4 replacement", content_type="application/pdf"),
            },
        )
        self.assertEqual(response.status_code, 403)
        protocol.refresh_from_db()
        self.assertEqual(protocol.original_name, "board.pdf")

    def test_invalid_protocol_extension_is_rejected_without_creating_row(self):
        self.poll.closed_at = timezone.now()
        self.poll._audit_actor = self.admin
        self.poll._audit_reason = "Закрытие для протокола"
        self.poll.save()
        self.login(self.admin)
        response = self.client.post(
            f"/work/governance/{self.poll.pk}/",
            {
                "action": "upload_protocol",
                "document": SimpleUploadedFile("board.exe", b"not allowed"),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(BoardProtocol.objects.filter(poll=self.poll).exists())
        self.assertContains(response, "PDF и DOCX")

    def test_workspace_does_not_add_legal_poll_fields(self):
        names = {field.name for field in BoardPoll._meta.fields}
        self.assertFalse({"quorum", "signature", "legal_result"} & names)
