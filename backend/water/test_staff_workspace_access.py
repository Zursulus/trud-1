from datetime import timedelta
from io import StringIO

from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .access_requests import ResidentAccessRequest
from .models import Account, ResidentAccess, ResidentInvite, ResidentPasswordReset, User


ADMIN = "Администратор ТСН"
PRIVATE = "Закрытый реестр членов ТСН"
OPERATOR = "Оператор воды"
CONTROLLER = "Контролёр воды"


class StaffWorkspaceAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.admin_user = User.objects.create_user(username="access-admin", is_staff=True)
        cls.admin_user.groups.add(Group.objects.get(name=ADMIN))
        cls.private_user = User.objects.create_user(username="access-private", is_staff=True)
        cls.private_user.groups.add(Group.objects.get(name=PRIVATE))
        cls.dual_user = User.objects.create_user(username="access-dual", is_staff=True)
        cls.dual_user.groups.add(Group.objects.get(name=ADMIN), Group.objects.get(name=PRIVATE))
        cls.operator = User.objects.create_user(username="access-operator", is_staff=True)
        cls.operator.groups.add(Group.objects.get(name=OPERATOR))
        cls.controller = User.objects.create_user(username="access-controller", is_staff=True)
        cls.controller.groups.add(Group.objects.get(name=CONTROLLER))

        cls.account = Account.objects.create(number="ACCESS-WS-1", plot="Тестовый участок доступа")
        cls.request_obj = ResidentAccessRequest.objects.create(
            full_name="Секретный Заявитель",
            email="secret-request@example.test",
            phone="+7 900 111-22-33",
            plot_hint="Тестовый участок доступа",
            claimed_role=ResidentAccessRequest.CLAIM_OWNER,
            message="Приватный комментарий заявки",
            submission_key="workspace-access-request",
        )
        cls.resident = User.objects.create_user(
            username="resident-access-workspace",
            email="resident-access@example.test",
            is_staff=False,
        )
        cls.access = ResidentAccess.objects.create(
            user=cls.resident,
            account=cls.account,
            role="owner",
            starts=timezone.localdate() - timedelta(days=10),
        )

    def login(self, user):
        self.client.force_login(user)

    def _new_request(self, suffix):
        return ResidentAccessRequest.objects.create(
            full_name=f"Заявитель {suffix}",
            email=f"request-{suffix}@example.test",
            phone="+7 900 000-00-01",
            plot_hint="Тестовый участок доступа",
            claimed_role=ResidentAccessRequest.CLAIM_OWNER,
            message="Проверка workflow",
            submission_key=f"request-{suffix}",
        )

    def test_admin_manages_access_but_cannot_see_or_open_request_pii(self):
        self.login(self.admin_user)
        response = self.client.get("/work/access/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Доступ жителей")
        self.assertContains(response, self.resident.username)
        self.assertNotContains(response, self.request_obj.full_name)
        self.assertNotContains(response, self.request_obj.email)
        self.assertNotContains(response, self.request_obj.phone)
        self.assertEqual(
            self.client.get(f"/work/access/requests/{self.request_obj.pk}/").status_code,
            403,
        )

    def test_private_registry_sees_request_can_reject_but_cannot_approve(self):
        request_obj = self._new_request("private-reject")
        self.login(self.private_user)
        dashboard = self.client.get("/work/access/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, request_obj.full_name)
        self.assertContains(dashboard, request_obj.email)
        self.assertNotContains(dashboard, self.resident.username)

        detail = self.client.get(f"/work/access/requests/{request_obj.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, request_obj.phone)
        self.assertContains(detail, "для одобрения требуется также полномочие")
        self.assertNotContains(detail, "Одобрить и создать приглашение")

        approve = self.client.post(
            f"/work/access/requests/{request_obj.pk}/",
            {
                "action": "approve",
                "approve-account": self.account.pk,
                "approve-role": "owner",
                "approve-email": request_obj.email,
                "approve-decision_note": "Проверено",
            },
        )
        self.assertEqual(approve.status_code, 403)
        self.assertEqual(ResidentInvite.objects.filter(access_request=request_obj).count(), 0)

        reject = self.client.post(
            f"/work/access/requests/{request_obj.pk}/",
            {"action": "reject", "reject-decision_note": "Основание не подтверждено"},
        )
        self.assertEqual(reject.status_code, 302)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, ResidentAccessRequest.STATUS_REJECTED)
        self.assertEqual(request_obj.decided_by, self.private_user)
        self.assertEqual(ResidentInvite.objects.filter(access_request=request_obj).count(), 0)

    def test_dual_authority_approves_once_creating_invite_not_access(self):
        request_obj = self._new_request("dual-approve")
        before_accesses = ResidentAccess.objects.count()
        self.login(self.dual_user)
        response = self.client.post(
            f"/work/access/requests/{request_obj.pk}/",
            {
                "action": "approve",
                "approve-account": self.account.pk,
                "approve-role": "owner",
                "approve-email": request_obj.email,
                "approve-decision_note": "Личность и счёт проверены",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Одноразовая ссылка создана")
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, ResidentAccessRequest.STATUS_APPROVED)
        self.assertEqual(request_obj.decided_by, self.dual_user)
        self.assertEqual(ResidentInvite.objects.filter(access_request=request_obj).count(), 1)
        self.assertEqual(ResidentAccess.objects.count(), before_accesses)

        repeat = self.client.post(
            f"/work/access/requests/{request_obj.pk}/",
            {
                "action": "approve",
                "approve-account": self.account.pk,
                "approve-role": "owner",
                "approve-email": request_obj.email,
                "approve-decision_note": "Повтор",
            },
        )
        self.assertEqual(repeat.status_code, 200)
        self.assertEqual(ResidentInvite.objects.filter(access_request=request_obj).count(), 1)

    def test_operator_and_controller_cannot_open_access_workspace(self):
        for user in (self.operator, self.controller):
            self.login(user)
            self.assertEqual(self.client.get("/work/access/").status_code, 403)
            self.assertEqual(
                self.client.get(f"/work/access/requests/{self.request_obj.pk}/").status_code,
                403,
            )

    def test_admin_can_end_access_issue_reset_and_revoke_reset_without_deleting_history(self):
        self.login(self.admin_user)
        reset_response = self.client.post(
            f"/work/access/accesses/{self.access.pk}/",
            {"action": "reset"},
        )
        self.assertEqual(reset_response.status_code, 200)
        self.assertContains(reset_response, "Одноразовая ссылка восстановления создана")
        reset = ResidentPasswordReset.objects.get(user=self.resident)

        revoke = self.client.post(f"/work/access/resets/{reset.pk}/revoke/")
        self.assertEqual(revoke.status_code, 302)
        reset.refresh_from_db()
        self.assertTrue(reset.revoked)

        end_date = timezone.localdate()
        end = self.client.post(
            f"/work/access/accesses/{self.access.pk}/",
            {"action": "end", "ends_on": end_date.isoformat()},
        )
        self.assertEqual(end.status_code, 302)
        self.access.refresh_from_db()
        self.assertEqual(self.access.ends, end_date)
        self.assertTrue(ResidentAccess.objects.filter(pk=self.access.pk).exists())

    def test_admin_model_uses_workflow_aware_registration_and_disables_manual_access_creation(self):
        model_admin = admin.site._registry[ResidentAccess]
        self.assertEqual(model_admin.__class__.__module__, "water.access_management_admin")
        request = RequestFactory().get("/admin/water/residentaccess/add/")
        request.user = self.admin_user
        self.assertFalse(model_admin.has_add_permission(request))
        readonly = model_admin.get_readonly_fields(request, self.access)
        for field in ("user", "account", "role", "starts", "ends"):
            self.assertIn(field, readonly)

    def test_access_pages_have_bounded_query_counts(self):
        self.login(self.dual_user)
        with CaptureQueriesContext(connection) as dashboard_queries:
            response = self.client.get("/work/access/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(dashboard_queries), 30, len(dashboard_queries))

        with CaptureQueriesContext(connection) as request_queries:
            response = self.client.get(f"/work/access/requests/{self.request_obj.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(request_queries), 18, len(request_queries))
