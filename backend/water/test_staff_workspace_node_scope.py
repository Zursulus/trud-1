from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .access_control import assignment_from_role, link_identity
from .access_policy import ScopeType
from .models import (
    Account, ControllerReadingSubmission, Membership, Meter, Person, SupplyNode, User, WaterGroup,
)


class NodeScopedStaffWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.admin = User.objects.create_user(
            username="node-scope-admin", is_staff=True, is_superuser=True,
        )
        cls.controller = User.objects.create_user(username="node-scope-controller")
        person = Person.objects.create(full_name="Synthetic node controller")
        link_identity(
            user=cls.controller, person=person, actor=cls.admin, basis="Synthetic test",
        )
        cls.node = SupplyNode.objects.create(name="Allowed test node")
        other_node = SupplyNode.objects.create(name="Other test node")
        cls.group = WaterGroup.objects.create(name="Allowed test line", node=cls.node)
        other_group = WaterGroup.objects.create(name="Other test line", node=other_node)
        cls.account = Account.objects.create(number="NODE-ALLOWED", plot="Allowed test plot")
        cls.other_account = Account.objects.create(number="NODE-OTHER", plot="Other test plot")
        for account, group, node in (
            (cls.account, cls.group, cls.node),
            (cls.other_account, other_group, other_node),
        ):
            Membership.objects.create(
                account=account, group=group, starts=today - timedelta(days=30),
            )
            Meter.objects.create(
                serial=f"METER-{account.number}", kind="individual", node=node, account=account,
            )
        assignment_from_role(
            person=person, role_code="controller", scope_type=ScopeType.SUPPLY_NODE,
            scope_object_id=cls.node.pk, basis="Synthetic test", granted_by=cls.admin,
        )
        cls.controller.refresh_from_db()

    def test_node_scoped_controller_can_search_and_open_account_card(self):
        self.client.force_login(self.controller)

        search = self.client.get("/work/search/", {"q": "NODE-"})
        self.assertEqual(search.status_code, 200)
        self.assertContains(search, self.account.number)
        self.assertNotContains(search, self.other_account.number)

        card = self.client.get(f"/work/accounts/{self.account.pk}/")
        self.assertEqual(card.status_code, 200)
        self.assertContains(card, self.group.name)
        self.assertContains(card, "METER-NODE-ALLOWED")
        self.assertNotContains(card, "Other test line")

        denied = self.client.get(f"/work/accounts/{self.other_account.pk}/")
        self.assertEqual(denied.status_code, 404)

    def test_node_moderator_home_counter_matches_the_available_review_queue(self):
        moderator = User.objects.create_user(username="node-scope-moderator")
        person = Person.objects.create(full_name="Synthetic node moderator")
        link_identity(user=moderator, person=person, actor=self.admin, basis="Synthetic test")
        assignment_from_role(
            person=person, role_code="water_moderator", scope_type=ScopeType.SUPPLY_NODE,
            scope_object_id=self.node.pk, basis="Synthetic test", granted_by=self.admin,
        )
        moderator.refresh_from_db()
        own_meter = Meter.objects.get(account=self.account)
        other_meter = Meter.objects.get(account=self.other_account)
        for meter in (own_meter, other_meter):
            ControllerReadingSubmission.objects.create(
                meter=meter, date=timezone.localdate(), value="10.000", submitted_by=self.controller,
            )
        ControllerReadingSubmission.objects.create(
            meter=own_meter, date=timezone.localdate(), value="11.000", submitted_by=self.controller,
            source=ControllerReadingSubmission.SOURCE_RESIDENT,
            line_review_status=ControllerReadingSubmission.LINE_REVIEW_PENDING,
        )
        self.client.force_login(moderator)

        home = self.client.get("/work/")
        self.assertEqual(home.status_code, 200)
        attention = [item for item in home.context["attention"] if item["url"] == "/work/water/"]
        self.assertEqual(len(attention), 1)
        self.assertEqual(attention[0]["count"], 1)

        water = self.client.get("/work/water/")
        self.assertEqual(water.status_code, 200)
        self.assertEqual(water.context["final_review_count"], attention[0]["count"])
        self.assertEqual(water.context["line_review_waiting_count"], 1)
        self.assertNotContains(water, other_meter.serial)
