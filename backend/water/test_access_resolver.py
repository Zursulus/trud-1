from datetime import date

from django.contrib.auth.models import Group, Permission
from django.test import TestCase

from .access_policy import ScopeType
from .access_resolver import explain
from .access_scope import ScopeRef
from .board_polls import BoardMembership
from .controller_scope import ControllerLineAccess
from .management.commands.setup_roles import Command as SetupRolesCommand
from .models import Account, Membership, Person, ResidentAccess, SupplyNode, User, WaterGroup
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity


class AccessResolverTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        SetupRolesCommand().handle()

    def setUp(self):
        self.node = SupplyNode.objects.create(name=f"Узел {self._testMethodName}")
        self.group = WaterGroup.objects.create(name=f"Линия {self._testMethodName}", node=self.node)
        self.account = Account.objects.create(number="A-1", plot="Дом")
        Membership.objects.create(account=self.account, group=self.group, starts=date(2026, 1, 1))

    def test_granular_portal_grant_explains_person_account_authority(self):
        user = User.objects.create_user(username="resident-grant")
        person = Person.objects.create(full_name="Житель Грант")
        ResidentIdentity.objects.create(user=user, person=person)
        PortalGrant.objects.create(
            person=person, account=self.account, starts=date(2026, 1, 1),
            can_view_account=True, can_submit_water=True, can_view_finance=False,
            basis="Тест", verified_by=User.objects.create_user(username="verifier", is_staff=True),
        )
        account_scope = ScopeRef(ScopeType.ACCOUNT, self.account.pk)
        water = explain(user, "resident.water.submit", scope=account_scope, on_date=date(2026, 5, 1))
        finance = explain(user, "resident.finance.view", scope=account_scope, on_date=date(2026, 5, 1))
        self.assertTrue(water.allowed)
        self.assertEqual(water.source, "portal_grant")
        self.assertEqual(water.person_id, person.pk)
        self.assertFalse(finance.allowed)

    def test_legacy_resident_access_keeps_compatibility(self):
        user = User.objects.create_user(username="legacy-resident")
        ResidentAccess.objects.create(
            user=user, account=self.account, role="owner", starts=date(2026, 1, 1),
        )
        decision = explain(
            user, "resident.finance.view",
            scope=ScopeRef(ScopeType.ACCOUNT, self.account.pk),
            on_date=date(2026, 5, 1),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.source, "portal_legacy")

    def test_line_assignment_covers_account_by_membership_date(self):
        user = User.objects.create_user(username="line-senior", is_staff=True)
        user.groups.add(Group.objects.get(name="Контролёр воды"))
        assignment = ControllerLineAccess.objects.create(
            user=user, group=self.group, starts=date(2026, 1, 1),
        )
        decision = explain(
            user, "water.observation.review_line",
            scope=ScopeRef(ScopeType.ACCOUNT, self.account.pk),
            on_date=date(2026, 5, 1),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.source, "legacy_controller_line_access")
        self.assertEqual(decision.source_id, assignment.pk)

    def test_line_assignment_does_not_leak_to_other_line(self):
        user = User.objects.create_user(username="line-senior-other", is_staff=True)
        user.groups.add(Group.objects.get(name="Контролёр воды"))
        ControllerLineAccess.objects.create(user=user, group=self.group, starts=date(2026, 1, 1))
        other_node = SupplyNode.objects.create(name="Другой узел")
        other_group = WaterGroup.objects.create(name="Другая линия", node=other_node)
        other_account = Account.objects.create(number="OTHER", plot="Чужой дом")
        Membership.objects.create(account=other_account, group=other_group, starts=date(2026, 1, 1))
        decision = explain(
            user, "water.observation.review_line",
            scope=ScopeRef(ScopeType.ACCOUNT, other_account.pk),
            on_date=date(2026, 5, 1),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.source, "deny_by_default")

    def test_global_django_permission_is_explained_as_compatibility_source(self):
        user = User.objects.create_user(username="appeals-staff", is_staff=True)
        user.user_permissions.add(Permission.objects.get(
            content_type__app_label="water", codename="view_residentappeal",
        ))
        decision = explain(user, "appeals.view", scope=ScopeRef(ScopeType.ALL))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.source, "django_permission_compat")

    def test_board_membership_is_workflow_entitlement_not_general_staff_role(self):
        user = User.objects.create_user(username="board-person")
        membership = BoardMembership.objects.create(
            user=user, role="member", starts=date(2026, 1, 1),
        )
        decision = explain(user, "governance.board.view", scope=ScopeRef(ScopeType.SELF, user.pk))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.source, "board_membership")
        self.assertEqual(decision.source_id, membership.pk)
        self.assertFalse(explain(user, "finance.view", scope=ScopeRef(ScopeType.ALL)).allowed)

    def test_inactive_user_is_denied_even_with_legacy_permission(self):
        user = User.objects.create_user(username="inactive", is_staff=True, is_active=False)
        user.user_permissions.add(Permission.objects.get(
            content_type__app_label="water", codename="view_residentappeal",
        ))
        decision = explain(user, "appeals.view", scope=ScopeRef(ScopeType.ALL))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.source, "inactive_user")

    def test_superuser_is_explicit_break_glass_source(self):
        user = User.objects.create_superuser(username="root-v2", password="x")
        decision = explain(user, "finance.payment.reverse", scope=ScopeRef(ScopeType.ALL))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.source, "superuser")

    def test_unrelated_business_fact_does_not_grant_account_authority(self):
        user = User.objects.create_user(username="plain-person")
        person = Person.objects.create(full_name="Просто человек")
        ResidentIdentity.objects.create(user=user, person=person)
        decision = explain(
            user, "resident.account.view",
            scope=ScopeRef(ScopeType.ACCOUNT, self.account.pk),
            on_date=date(2026, 5, 1),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.source, "deny_by_default")
