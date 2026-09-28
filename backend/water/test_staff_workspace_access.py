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
from .models import Account, Person, ResidentAccess, ResidentInvite, ResidentPasswordReset, User
from .portal import issue_granular_invite
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity


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


    def test_dual_authority_can_create_person_then_continue_to_grant_form(self):
        self.login(self.dual_user)
        response = self.client.post(
            "/work/access/people/new/",
            {
                "full_name": "Новый Тестовый Житель",
                "email": "new-person@example.test",
                "phone": "+7 900 000-00-02",
                "notes": "Синтетическая тестовая карточка",
            },
        )
        self.assertEqual(response.status_code, 302)
        person = Person.objects.get(email="new-person@example.test")
        self.assertIn(f"person={person.pk}", response["Location"] )
        self.assertIn("email=new-person%40example.test", response["Location"] )
        follow = self.client.get(response["Location"])
        self.assertEqual(follow.status_code, 200)
        self.assertEqual(follow.context["form"].initial["person"], str(person.pk))
        self.assertEqual(follow.context["form"].initial["email"], person.email)

    def test_dual_authority_can_issue_granular_invite_and_activation_creates_identity_and_grant(self):
        person = Person.objects.create(full_name="Тестовый Житель", email="granular@example.test")
        self.login(self.dual_user)
        response = self.client.post(
            "/work/access/invite/",
            {
                "person": person.pk,
                "account": self.account.pk,
                "email": "granular@example.test",
                "basis": "Личность и основание проверены",
                "can_view_account": "on",
                "can_submit_water": "on",
                "can_view_documents": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        invite = ResidentInvite.objects.get(email="granular@example.test")
        self.assertTrue(invite.is_granular)
        self.assertFalse(invite.role)
        self.assertContains(response, "Одноразовая ссылка создана")
        invite_url = response.context["invite_url"]
        self.assertTrue(invite_url)
        self.assertFalse(PortalGrant.objects.filter(person=person, account=self.account).exists())

        before_legacy = ResidentAccess.objects.count()
        self.client.logout()
        response = self.client.post(
            invite_url,
            {"password1": "Granular-resident-2026!", "password2": "Granular-resident-2026!"},
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="granular@example.test")
        identity = ResidentIdentity.objects.get(user=user)
        self.assertEqual(identity.person, person)
        grant = PortalGrant.objects.get(person=person, account=self.account)
        self.assertTrue(grant.can_view_account)
        self.assertTrue(grant.can_submit_water)
        self.assertTrue(grant.can_view_documents)
        self.assertFalse(grant.can_view_finance)
        self.assertEqual(ResidentAccess.objects.count(), before_legacy)
        invite.refresh_from_db()
        self.assertIsNotNone(invite.used_at)

    def test_granular_invite_requires_both_private_registry_and_access_authority(self):
        for user in (self.admin_user, self.private_user):
            self.login(user)
            self.assertEqual(self.client.get("/work/access/invite/").status_code, 403)
        self.login(self.dual_user)
        self.assertEqual(self.client.get("/work/access/invite/").status_code, 200)

    def test_existing_user_without_current_access_can_log_in_only_to_activate_matching_invite(self):
        person = Person.objects.create(full_name="Возвращающийся житель")
        dormant = User.objects.create_user(
            username="returning-resident", email="returning@example.test", password="Returning-resident-2026!",
        )
        _invite, raw = issue_granular_invite(
            self.account, person, dormant.email, "Повторно проверено", actor=self.dual_user,
            can_view_finance=True,
        )
        invite_path = f"/admin/cabinet/invite/{raw}/"
        denied = self.client.post(
            "/admin/cabinet/login/",
            {"username": dormant.username, "password": "Returning-resident-2026!"},
        )
        self.assertEqual(denied.status_code, 200)
        self.assertContains(denied, "Нет действующего доступа")
        allowed = self.client.post(
            f"/admin/cabinet/login/?next={invite_path}",
            {"username": dormant.username, "password": "Returning-resident-2026!", "next": invite_path},
        )
        self.assertEqual(allowed.status_code, 302)
        self.assertEqual(allowed["Location"], invite_path)

    def test_granular_invite_does_not_rebind_existing_identity(self):
        original_person = Person.objects.create(full_name="Уже подтверждённый житель")
        invited_person = Person.objects.create(full_name="Другой проверенный житель")
        ResidentIdentity.objects.create(
            user=self.resident, person=original_person, verified_by=self.dual_user, basis="Ранее проверено",
        )
        invite, raw = issue_granular_invite(
            self.account, invited_person, self.resident.email, "Новая проверка", actor=self.dual_user,
        )
        self.login(self.resident)
        response = self.client.post(f"/admin/cabinet/invite/{raw}/")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(PortalGrant.objects.filter(person=invited_person).exists())
        invite.refresh_from_db()
        self.assertIsNone(invite.used_at)
        self.assertEqual(ResidentIdentity.objects.get(user=self.resident).person, original_person)

    def test_admin_can_manage_explicit_grant_without_private_person_name(self):
        person = Person.objects.create(full_name="Скрытое Имя")
        ResidentIdentity.objects.create(
            user=self.resident, person=person, verified_by=self.dual_user, basis="Проверено",
        )
        grant = PortalGrant.objects.create(
            person=person, account=self.account, starts=timezone.localdate() - timedelta(days=2),
            basis="Проверено", verified_by=self.dual_user, can_submit_water=True,
        )
        self.login(self.admin_user)
        dashboard = self.client.get("/work/access/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Точечные права")
        self.assertNotContains(dashboard, person.full_name)

        reset = self.client.post(f"/work/access/grants/{grant.pk}/", {"action": "reset"})
        self.assertEqual(reset.status_code, 200)
        self.assertContains(reset, "Одноразовая ссылка восстановления создана")
        self.assertTrue(ResidentPasswordReset.objects.filter(user=self.resident).exists())

        end_on = timezone.localdate()
        end = self.client.post(
            f"/work/access/grants/{grant.pk}/", {"action": "end", "ends_on": end_on.isoformat()},
        )
        self.assertEqual(end.status_code, 302)
        grant.refresh_from_db()
        self.assertEqual(grant.ends, end_on)

    def test_staff_without_personal_grant_gets_normal_no_access_explanation(self):
        self.login(self.admin_user)
        response = self.client.get("/admin/cabinet/")
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Доступ к участку не найден", status_code=403)
        self.assertNotContains(response, "Сейчас вы вошли как сотрудник", status_code=403)

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
