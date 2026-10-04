from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .access_control import assignment_from_role, link_identity
from .access_policy import ScopeType
from .access_resolver import can, scopes_for
from .access_scope import ScopeRef
from .models import (
    Account, ControllerReadingSubmission, Membership, Meter, Person, Reading,
    SupplyNode, User, WaterGroup,
)


class StaffModerationScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.today = timezone.localdate()
        cls.admin = User.objects.create_user(
            username="moderation-scope-admin", is_staff=True, is_superuser=True,
        )
        cls.resident = User.objects.create_user(username="moderation-scope-resident")
        cls.nodes = {}
        cls.accounts = {}
        cls.meters = {}
        cls.ready = {}
        for label in ("A", "B"):
            node = SupplyNode.objects.create(name=f"Synthetic moderation node {label}")
            group = WaterGroup.objects.create(name=f"Synthetic moderation line {label}", node=node)
            account = Account.objects.create(number=f"MOD-SCOPE-{label}", plot=f"Synthetic plot {label}")
            Membership.objects.create(
                account=account, group=group,
                starts=cls.today - timedelta(days=30), ends=cls.today + timedelta(days=30),
            )
            meter = Meter.objects.create(
                serial=f"MOD-SCOPE-METER-{label}", kind="individual", node=node, account=account,
                commissioned_on=cls.today - timedelta(days=30),
            )
            cls.nodes[label], cls.accounts[label], cls.meters[label] = node, account, meter
            cls.ready[label] = ControllerReadingSubmission.objects.create(
                meter=meter, date=cls.today, value="10.000", submitted_by=cls.admin,
                source=ControllerReadingSubmission.SOURCE_CONTROLLER,
                line_review_status=ControllerReadingSubmission.LINE_REVIEW_NOT_REQUIRED,
            )
            ControllerReadingSubmission.objects.create(
                meter=meter, date=cls.today, value="11.000", submitted_by=cls.resident,
                source=ControllerReadingSubmission.SOURCE_RESIDENT,
                line_review_status=ControllerReadingSubmission.LINE_REVIEW_PENDING,
            )

    def _actor(self, name, roles):
        user = User.objects.create_user(username=f"moderation-scope-{name}")
        person = Person.objects.create(full_name=f"Synthetic moderation actor {name}")
        link_identity(user=user, person=person, actor=self.admin, basis="Synthetic test")
        for role, label, starts, ends in roles:
            assignment_from_role(
                person=person, role_code=role,
                scope_type=ScopeType.ALL if label is None else ScopeType.SUPPLY_NODE,
                scope_object_id=None if label is None else self.nodes[label].pk,
                starts=starts, ends=ends, basis="Synthetic test", granted_by=self.admin,
            )
        user.refresh_from_db()
        return user

    def _active_role(self, role, label):
        return role, label, self.today - timedelta(days=15), self.today + timedelta(days=15)

    def _assert_review_queue(self, user, allowed_labels):
        self.client.force_login(user)
        home = self.client.get("/work/")
        self.assertEqual(home.status_code, 200)
        attention = [item for item in home.context["attention"] if item["url"] == "/work/water/"]
        self.assertEqual(len(attention), 1)
        water = self.client.get("/work/water/")
        self.assertEqual(water.status_code, 200)
        self.assertTrue(water.context["can_moderate_submissions"])
        expected_ids = {self.ready[label].pk for label in allowed_labels}
        # Keep the independent home, queue and waiting-count failures visible on the old code.
        with self.subTest(state="home_count"):
            self.assertEqual(attention[0]["count"], len(expected_ids))
        with self.subTest(state="final_review_count"):
            self.assertEqual(water.context["final_review_count"], len(expected_ids))
        with self.subTest(state="final_review_items"):
            self.assertEqual({item.pk for item in water.context["final_review_items"]}, expected_ids)
        with self.subTest(state="line_review_waiting_count"):
            self.assertEqual(water.context["line_review_waiting_count"], len(expected_ids))
        for label in allowed_labels:
            self.assertContains(water, self.meters[label].serial)
        for label in set(self.meters) - set(allowed_labels):
            with self.subTest(state="rendered_queue", excluded_node=label):
                self.assertNotContains(water, self.meters[label].serial)
        return water

    def _assert_view_preserved_and_finalization_denied(self, user, label):
        scope = ScopeRef(ScopeType.SUPPLY_NODE, self.nodes[label].pk)
        self.assertTrue(can(user, "water.meters.view", scope=scope, on_date=self.today))
        self.assertFalse(can(user, "water.observation.finalize", scope=scope, on_date=self.today))
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        card = client.get(f"/work/accounts/{self.accounts[label].pk}/")
        self.assertEqual(card.status_code, 200)
        self.assertContains(card, self.meters[label].serial)
        self.assertIn("csrftoken", client.cookies)
        csrf = client.cookies["csrftoken"].value
        submission = self.ready[label]
        history_count = submission.history.count()
        for action in ("approve", "reject"):
            response = client.post(
                reverse(f"admin:water_controllerreading_{action}", args=[submission.pk]),
                {"csrfmiddlewaretoken": csrf, "review_comment": "Synthetic denied attempt"},
            )
            self.assertEqual(response.status_code, 403, action)
        submission.refresh_from_db()
        self.assertEqual(submission.status, "pending")
        self.assertIsNone(submission.reviewed_by_id)
        self.assertIsNone(submission.reviewed_at)
        self.assertIsNone(submission.reading_id)
        self.assertEqual(submission.review_comment, "")
        self.assertEqual(submission.history.count(), history_count)
        self.assertFalse(Reading.objects.filter(meter=submission.meter, date=self.today).exists())

    def test_moderator_a_controller_b_queue_only_contains_finalizable_a(self):
        user = self._actor("mixed-a-b", [
            self._active_role("water_moderator", "A"), self._active_role("controller", "B"),
        ])
        self._assert_view_preserved_and_finalization_denied(user, "B")
        self._assert_review_queue(user, {"A"})

    def test_moderator_b_controller_a_queue_only_contains_finalizable_b(self):
        user = self._actor("mixed-b-a", [
            self._active_role("water_moderator", "B"), self._active_role("controller", "A"),
        ])
        self._assert_view_preserved_and_finalization_denied(user, "A")
        self._assert_review_queue(user, {"B"})

    def test_pure_node_moderator_keeps_scoped_queue(self):
        user = self._actor("pure-a", [self._active_role("water_moderator", "A")])
        self._assert_review_queue(user, {"A"})
        self.assertEqual(self.client.get(f"/work/accounts/{self.accounts['B'].pk}/").status_code, 404)

    def test_two_moderator_assignments_union_both_finalization_scopes(self):
        user = self._actor("both", [
            self._active_role("water_moderator", "A"), self._active_role("water_moderator", "B"),
        ])
        self._assert_review_queue(user, {"A", "B"})

    def test_global_v2_role_and_node_controller_keep_global_queue(self):
        user = self._actor("global-v2", [
            self._active_role("tsn_admin", None), self._active_role("controller", "B"),
        ])
        self.assertIn(ScopeRef(ScopeType.ALL), scopes_for(user, "water.observation.finalize"))
        self._assert_review_queue(user, {"A", "B"})

    def test_legacy_global_permission_and_v2_node_moderator_keep_global_queue(self):
        user = self._actor("legacy-global", [self._active_role("water_moderator", "A")])
        user.groups.add(Group.objects.get(name="Администратор ТСН"))
        self.assertTrue(user.has_perm("water.change_controllerreadingsubmission"))
        self.assertIn(ScopeRef(ScopeType.ALL), scopes_for(user, "water.observation.finalize"))
        self._assert_review_queue(user, {"A", "B"})

    def test_superuser_keeps_global_queue(self):
        self._assert_review_queue(self.admin, {"A", "B"})

    def test_bare_staff_without_assignments_cannot_view_or_finalize_water(self):
        user = User.objects.create_user(username="moderation-scope-bare-staff", is_staff=True)
        self.assertEqual(scopes_for(user, "water.observation.finalize"), [])
        client = Client(enforce_csrf_checks=True)
        client.force_login(user)
        home = client.get("/work/")
        self.assertEqual(home.status_code, 200)
        self.assertEqual(home.context["account_count"], 0)
        self.assertEqual(home.context["attention"], [])
        self.assertEqual(client.get("/work/water/").status_code, 403)
        response = client.post(
            reverse("admin:water_controllerreading_approve", args=[self.ready["A"].pk]),
            {"csrfmiddlewaretoken": client.cookies["csrftoken"].value},
        )
        self.assertEqual(response.status_code, 403)
        self.ready["A"].refresh_from_db()
        self.assertEqual(self.ready["A"].status, "pending")
        self.assertFalse(Reading.objects.exists())

    def test_expired_and_future_moderator_assignments_do_not_authorize_today(self):
        user = self._actor("inactive-moderation", [
            self._active_role("controller", "B"),
            ("water_moderator", "A", self.today - timedelta(days=15), self.today),
            ("water_moderator", "A", self.today + timedelta(days=1), self.today + timedelta(days=15)),
        ])
        self.assertEqual(scopes_for(user, "water.observation.finalize", on_date=self.today), [])
        self._assert_view_preserved_and_finalization_denied(user, "B")
        self.client.force_login(user)
        home = self.client.get("/work/")
        self.assertEqual(home.status_code, 200)
        self.assertFalse(any(item["url"] == "/work/water/" for item in home.context["attention"]))
        water = self.client.get("/work/water/")
        self.assertEqual(water.status_code, 200)
        self.assertFalse(water.context["can_moderate_submissions"])
        self.assertNotIn("final_review_items", water.context)

    def test_global_queue_excludes_not_yet_commissioned_and_retired_meters(self):
        for name, dates in (
            ("future", {"commissioned_on": self.today + timedelta(days=1)}),
            ("retired", {"commissioned_on": self.today - timedelta(days=30),
                         "retired_on": self.today - timedelta(days=1)}),
        ):
            meter = Meter.objects.create(
                serial=f"MOD-SCOPE-INACTIVE-{name}", kind="individual", node=self.nodes["A"],
                account=self.accounts["A"], **dates,
            )
            if name == "retired":
                ControllerReadingSubmission.objects.create(
                    meter=meter, date=self.today - timedelta(days=2),
                    value="12.000", submitted_by=self.admin,
                )
        water = self._assert_review_queue(self.admin, {"A", "B"})
        self.assertEqual(water.context["meter_count"], 2)
        self.assertNotContains(water, "MOD-SCOPE-INACTIVE-")
