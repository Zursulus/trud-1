from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .models import Account, AppealCategory, ResidentAccess, ResidentAppeal, User
from .resident_models import ResidentAppealBoardMessage


class StaffWorkspaceAppealTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.today = timezone.localdate()
        cls.manager = User.objects.create_user(username="appeal-manager", is_staff=True)
        cls.manager.groups.add(Group.objects.get(name="Администратор ТСН"))
        cls.operator = User.objects.create_user(username="appeal-operator", is_staff=True)
        cls.operator.groups.add(Group.objects.get(name="Оператор воды"))
        cls.private_user = User.objects.create_user(username="appeal-private", is_staff=True)
        cls.private_user.groups.add(Group.objects.get(name="Закрытый реестр членов ТСН"))
        cls.viewer = User.objects.create_user(username="appeal-viewer", is_staff=True)
        cls.viewer.user_permissions.add(Permission.objects.get(codename="view_residentappeal"))

        cls.resident = User.objects.create_user(
            username="secret-resident@example.test",
            email="secret-resident@example.test",
        )
        cls.account = Account.objects.create(number="AP-101", plot="Садовая 101")
        ResidentAccess.objects.create(
            user=cls.resident,
            account=cls.account,
            role="owner",
            starts=cls.today - timedelta(days=30),
        )
        cls.category = AppealCategory.objects.create(name="Документы")
        cls.appeal = ResidentAppeal.objects.create(
            account=cls.account,
            author=cls.resident,
            category=cls.category,
            subject="Нужна справка",
            message="Прошу подготовить справку по участку.",
        )
        cls.resolved = ResidentAppeal.objects.create(
            account=cls.account,
            author=cls.resident,
            category=cls.category,
            subject="Старый вопрос",
            message="Ранее заданный вопрос.",
            status="resolved",
            response="Вопрос уже решён.",
            responded_at=timezone.now(),
            responded_by=cls.manager,
        )

    def login(self, user):
        self.client.force_login(user)

    def test_manager_queue_and_detail_hide_author_identity(self):
        self.login(self.manager)
        queue = self.client.get("/work/appeals/")
        self.assertEqual(queue.status_code, 200)
        self.assertContains(queue, "Нужна справка")
        self.assertContains(queue, "Садовая 101")
        self.assertContains(queue, "AP-101")
        self.assertNotContains(queue, "secret-resident@example.test")
        self.assertNotContains(queue, "Старый вопрос")

        detail = self.client.get(f"/work/appeals/{self.appeal.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Прошу подготовить справку по участку.")
        self.assertContains(detail, "Ответить жителю")
        self.assertNotContains(detail, "secret-resident@example.test")

    def test_filters_search_and_account_scope_are_server_side(self):
        self.login(self.manager)
        resolved = self.client.get("/work/appeals/", {"state": "resolved"})
        self.assertContains(resolved, "Старый вопрос")
        self.assertNotContains(resolved, "Нужна справка")

        search = self.client.get("/work/appeals/", {"state": "all", "q": "AP-101"})
        self.assertContains(search, "Нужна справка")
        self.assertContains(search, "Старый вопрос")

        account = self.client.get("/work/appeals/", {"state": "all", "account": self.account.pk})
        self.assertEqual(account.status_code, 200)
        self.assertEqual(account.context["page"].paginator.count, 2)

    def test_non_appeal_roles_are_denied_and_viewer_is_read_only(self):
        for user in (self.operator, self.private_user):
            self.login(user)
            self.assertEqual(self.client.get("/work/appeals/").status_code, 403)
            self.assertEqual(self.client.get(f"/work/appeals/{self.appeal.pk}/").status_code, 403)

        self.login(self.viewer)
        detail = self.client.get(f"/work/appeals/{self.appeal.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Только просмотр")
        self.assertNotContains(detail, "Ответить жителю")
        post = self.client.post(
            f"/work/appeals/{self.appeal.pk}/",
            {"action": "reply", "body": "Нельзя отправить", "next_status": "in_progress"},
        )
        self.assertEqual(post.status_code, 403)

    def test_staff_reply_waiting_then_resident_reply_returns_to_work(self):
        self.login(self.manager)
        response = self.client.post(
            f"/work/appeals/{self.appeal.pk}/",
            {
                "action": "reply",
                "body": "Уточните номер документа.",
                "next_status": "awaiting_resident",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.appeal.refresh_from_db()
        self.assertEqual(self.appeal.status, "awaiting_resident")
        self.assertEqual(
            ResidentAppealBoardMessage.objects.get(appeal=self.appeal).body,
            "Уточните номер документа.",
        )

        self.login(self.resident)
        response = self.client.post(
            f"/admin/cabinet/account/{self.account.pk}/appeal/{self.appeal.pk}/",
            {"body": "Номер документа 42."},
        )
        self.assertEqual(response.status_code, 302)
        self.appeal.refresh_from_db()
        self.assertEqual(self.appeal.status, "in_progress")

    def test_final_reply_is_resolved_and_rendered_once_for_resident(self):
        self.login(self.manager)
        response = self.client.post(
            f"/work/appeals/{self.appeal.pk}/",
            {
                "action": "reply",
                "body": "Итоговый ответ по обращению.",
                "next_status": "resolved",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.appeal.refresh_from_db()
        self.assertEqual(self.appeal.status, "resolved")
        self.assertEqual(self.appeal.response, "Итоговый ответ по обращению.")
        self.assertEqual(self.appeal.responded_by, self.manager)
        self.assertIsNotNone(self.appeal.responded_at)
        self.assertFalse(ResidentAppealBoardMessage.objects.filter(appeal=self.appeal).exists())

        self.login(self.resident)
        resident_view = self.client.get(
            f"/admin/cabinet/account/{self.account.pk}/appeal/{self.appeal.pk}/"
        )
        self.assertEqual(resident_view.status_code, 200)
        self.assertContains(resident_view, "Итоговый ответ по обращению.", count=1)

    def test_resolved_appeal_can_be_closed_but_closed_dialog_cannot_receive_reply(self):
        self.login(self.manager)
        response = self.client.post(
            f"/work/appeals/{self.resolved.pk}/",
            {"action": "close"},
        )
        self.assertEqual(response.status_code, 302)
        self.resolved.refresh_from_db()
        self.assertEqual(self.resolved.status, "closed")

        response = self.client.post(
            f"/work/appeals/{self.resolved.pk}/",
            {"action": "reply", "body": "Поздний ответ", "next_status": "in_progress"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Обращение уже завершено")
        self.assertFalse(ResidentAppealBoardMessage.objects.filter(appeal=self.resolved).exists())

    def test_dashboard_and_account_card_link_to_workspace_appeals(self):
        self.login(self.manager)
        dashboard = self.client.get("/work/")
        self.assertContains(dashboard, "/work/appeals/?state=open")
        card = self.client.get(f"/work/accounts/{self.account.pk}/")
        self.assertContains(card, f"/work/appeals/?state=open&account={self.account.pk}")

    def test_queue_and_detail_have_bounded_query_counts(self):
        self.login(self.manager)
        with CaptureQueriesContext(connection) as queue_queries:
            response = self.client.get("/work/appeals/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queue_queries), 18, len(queue_queries))

        with CaptureQueriesContext(connection) as detail_queries:
            response = self.client.get(f"/work/appeals/{self.appeal.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(detail_queries), 22, len(detail_queries))
