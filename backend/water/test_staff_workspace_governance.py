from datetime import timedelta
import tempfile

from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .board_polls import BoardAuditEvent, BoardPoll, BoardProtocol, BoardQuestion
from .management.commands.setup_roles import ADMIN
from .models import User


class StaffWorkspaceGovernanceTests(TestCase):
    def setUp(self):
        from django.core.management import call_command

        call_command("setup_roles", verbosity=0)
        self.media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media.cleanup)

        self.admin = User.objects.create_user(username="governance-admin", password="x", is_staff=True)
        self.admin.groups.add(Group.objects.get(name=ADMIN))
        self.limited = User.objects.create_user(username="governance-limited", password="x", is_staff=True)
        self.now = timezone.now().replace(second=0, microsecond=0)

    def _create_via_workspace(self, *, title="Проверочный опрос"):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("staff_workspace:governance_poll_create"), {
            "title": title,
            "description": "Только предварительное обсуждение",
            "opens_at": self.now.strftime("%Y-%m-%dT%H:%M"),
            "closes_at": (self.now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
            "questions": "Первый вопрос?\nВторой вопрос?",
        })
        self.assertEqual(response.status_code, 302)
        return BoardPoll.objects.get(title=title)

    def test_workspace_requires_board_permissions(self):
        self.client.force_login(self.limited)
        self.assertEqual(self.client.get(reverse("staff_workspace:governance")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("staff_workspace:governance")).status_code, 200)

    def test_more_links_to_governance_for_tsn_admin(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("staff_workspace:more"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Опросы / правление")
        self.assertContains(response, reverse("staff_workspace:governance"))

    def test_create_reuses_existing_models_and_audits_questions(self):
        poll = self._create_via_workspace()
        questions = list(poll.questions.order_by("order"))
        self.assertEqual([question.text for question in questions], ["Первый вопрос?", "Второй вопрос?"])
        self.assertEqual(BoardPoll.objects.count(), 1)
        self.assertEqual(BoardQuestion.objects.count(), 2)
        self.assertEqual(
            BoardAuditEvent.objects.filter(target_type="boardpoll", target_id=poll.pk).count(),
            1,
        )
        for question in questions:
            self.assertEqual(
                BoardAuditEvent.objects.filter(target_type="boardquestion", target_id=question.pk).count(),
                1,
            )
        field_names = {field.name for field in BoardPoll._meta.fields}
        self.assertFalse({"quorum", "signature", "legal_result"} & field_names)

    def test_close_uses_optimistic_version_and_preserves_audit(self):
        poll = self._create_via_workspace()
        original_version = poll.version
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("staff_workspace:governance_poll", args=[poll.pk]),
            {"action": "close", "version": original_version},
        )
        self.assertEqual(response.status_code, 302)
        poll.refresh_from_db()
        self.assertIsNotNone(poll.closed_at)
        self.assertGreater(poll.version, original_version)
        event = BoardAuditEvent.objects.filter(target_type="boardpoll", target_id=poll.pk).order_by("-id").first()
        self.assertIn("Закрытие", event.summary)

    def test_stale_close_is_rejected_without_closing(self):
        poll = self._create_via_workspace(title="Конкурентное изменение")
        stale_version = poll.version
        poll.description = "Параллельное изменение"
        poll._audit_actor = self.admin
        poll._audit_reason = "Параллельная правка"
        poll.save()

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("staff_workspace:governance_poll", args=[poll.pk]),
            {"action": "close", "version": stale_version},
        )
        self.assertEqual(response.status_code, 400)
        poll.refresh_from_db()
        self.assertIsNone(poll.closed_at)
        self.assertContains(response, "Опрос уже изменён", status_code=400)

    def test_closed_poll_accepts_one_private_immutable_protocol(self):
        poll = self._create_via_workspace(title="Опрос с протоколом")
        poll.refresh_from_db()
        poll.closed_at = timezone.now()
        poll._audit_actor = self.admin
        poll._audit_reason = "Подготовка протокола"
        poll.save()

        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("staff_workspace:governance_poll", args=[poll.pk]),
            {
                "action": "protocol",
                "document": SimpleUploadedFile(
                    "protocol.pdf", b"%PDF-1.4\nworkspace-governance-test", content_type="application/pdf"
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        protocol = BoardProtocol.objects.get(poll=poll)
        self.assertEqual(protocol.original_name, "protocol.pdf")

        download = self.client.get(reverse("staff_workspace:governance_protocol", args=[poll.pk]))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Cache-Control"], "private, no-store")
        self.assertEqual(download["X-Content-Type-Options"], "nosniff")

        replacement = self.client.post(
            reverse("staff_workspace:governance_poll", args=[poll.pk]),
            {
                "action": "protocol",
                "document": SimpleUploadedFile(
                    "replacement.pdf", b"%PDF-1.4\nreplacement", content_type="application/pdf"
                ),
            },
        )
        self.assertEqual(replacement.status_code, 400)
        self.assertEqual(BoardProtocol.objects.filter(poll=poll).count(), 1)
        protocol.refresh_from_db()
        self.assertEqual(protocol.original_name, "protocol.pdf")
