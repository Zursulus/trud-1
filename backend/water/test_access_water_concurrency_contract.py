"""Real HTTP claims and moderation on independently blocked PostgreSQL connections."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest import skipUnless

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import connection
from django.test import Client, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .controller_scope import ControllerLineAccess
from .models import Account, ControllerReadingSubmission, Membership, Meter, Person, Reading, ResidentAccess, ResidentInvite, SupplyNode, User, WaterGroup
from .portal import issue_granular_invite
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity
from .test_payment_concurrency_contract import PostgreSQLConcurrencyMixin


@skipUnless(connection.vendor == "postgresql", "Cross-role races require PostgreSQL")
class AccessWaterConcurrencyContractTests(PostgreSQLConcurrencyMixin, TransactionTestCase):
    password = "synthetic-contract-password"

    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.today = timezone.localdate()
        self.account = Account.objects.create(number="RACE-ACCESS-WATER")
        self.staff = [User.objects.create_user(username=f"race-moderator-{i}", password=self.password, is_staff=True) for i in range(2)]
        group = Group.objects.get(name="Администратор ТСН")
        for actor in self.staff:
            actor.groups.add(group)
            self.assertTrue(actor.has_perm("water.change_controllerreadingsubmission"))

    def _form_client(self, url):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get(url).status_code, 200)
        self.assertIn("csrftoken", client.cookies)
        return client

    def _post(self, client, url, data=None):
        return client.post(url, {**(data or {}), "csrfmiddlewaretoken": client.cookies["csrftoken"].value})

    def _login_client(self, actor):
        url = "/admin/login/" if actor.is_staff else "/admin/cabinet/login/"
        client = self._form_client(url)
        self.assertEqual(self._post(client, url, {"username": actor.username, "password": self.password}).status_code, 302)
        self.assertEqual(int(client.session["_auth_user_id"]), actor.pk)
        return client

    def test_two_redemptions_consume_one_granular_invite_once(self):
        person = Person.objects.create(full_name="Synthetic concurrent resident")
        invite, raw = issue_granular_invite(self.account, person, "race-invite@example.invalid", "Synthetic verified basis",
                                           actor=self.staff[0], can_submit_water=True, can_use_appeals=True)
        url = reverse("resident_invite", args=[raw])
        clients = [self._form_client(url) for _ in range(2)]
        users_before = User.objects.count()

        def redeem(client):
            return self._post(client, url, {"password1": self.password, "password2": self.password}).status_code

        results = self._concurrent_actions(ResidentInvite, invite.pk, [lambda c=c: redeem(c) for c in clients])
        self.assertEqual(sorted(results), [302, 410])
        self.assertEqual(User.objects.count(), users_before + 1)
        user = User.objects.get(email="race-invite@example.invalid")
        self.assertTrue(user.check_password(self.password))
        self.assertEqual(sorted(int(c.session.get("_auth_user_id", 0)) for c in clients), [0, user.pk])
        identity = ResidentIdentity.objects.get(person=person)
        self.assertEqual((identity.user_id, identity.verified_by_id, identity.basis), (user.pk, self.staff[0].pk, "Synthetic verified basis"))
        grant = PortalGrant.objects.get(person=person, account=self.account)
        self.assertEqual((grant.starts, grant.ends, grant.can_view_account, grant.can_view_finance, grant.can_submit_water,
                          grant.can_view_documents, grant.can_use_appeals, grant.can_represent),
                         (self.today, None, True, False, True, False, True, False))
        self.assertFalse(ResidentAccess.objects.filter(user=user).exists())
        self.assertEqual(list(identity.history.values_list("history_user_id", flat=True)), [self.staff[0].pk])
        self.assertEqual(list(grant.history.values_list("history_user_id", flat=True)), [self.staff[0].pk])
        invite.refresh_from_db()
        self.assertIsNotNone(invite.used_at)
        self.assertEqual([(h.used_at is not None, h.history_user_id) for h in invite.history.order_by("history_date", "history_id")],
                         [(False, self.staff[0].pk), (True, user.pk)])
        self.assertEqual([c.get(url).status_code for c in clients], [410, 410])
        self.assertEqual(invite.history.count(), 2)

    def test_distinct_moderators_accept_one_observation_once(self):
        node = SupplyNode.objects.create(name="Synthetic race node")
        group = WaterGroup.objects.create(name="Synthetic race line", node=node)
        Membership.objects.create(account=self.account, group=group, starts=self.today - timedelta(days=30))
        meter = Meter.objects.create(serial="RACE-METER", kind="individual", node=node, account=self.account,
                                     commissioned_on=self.today - timedelta(days=30))
        previous = Reading.objects.create(meter=meter, date=self.today - timedelta(days=1), value=Decimal("100.000"))
        resident = User.objects.create_user(username="race-water-resident", password=self.password)
        ResidentAccess.objects.create(user=resident, account=self.account, role="owner", starts=self.today - timedelta(days=30))
        resident_client = self._login_client(resident)
        self.assertEqual(self._post(resident_client, f"/admin/cabinet/account/{self.account.pk}/meter/{meter.pk}/reading/", {
            "date": self.today.isoformat(), "value": "120.000", "notes": "Synthetic observation",
        }).status_code, 302)
        observation = ControllerReadingSubmission.objects.get(meter=meter, date=self.today)
        senior = User.objects.create_user(username="race-line-senior", password=self.password, is_staff=True)
        senior.user_permissions.add(Permission.objects.get(content_type__app_label="water", codename="use_controller_workspace"))
        ControllerLineAccess.objects.create(user=senior, group=group, starts=self.today - timedelta(days=30))
        senior_client = self._login_client(senior)
        self.assertEqual(self._post(senior_client, "/work/water/line/", {
            "date": self.today.isoformat(), "review_submission": observation.pk, "decision": "flag", "line_review_comment": "Synthetic check",
        }).status_code, 302)
        observation.refresh_from_db()
        self.assertEqual((observation.status, observation.line_review_status), ("pending", "flagged"))
        before_history = list(observation.history.order_by("history_date", "history_id").values_list("status", "history_user_id"))
        self.assertEqual(before_history, [("pending", resident.pk), ("pending", senior.pk)])
        clients = [self._login_client(actor) for actor in self.staff]
        url = reverse("admin:water_controllerreading_approve", args=[observation.pk])
        results = self._concurrent_actions(ControllerReadingSubmission, observation.pk, [lambda c=c: self._post(c, url).status_code for c in clients])
        self.assertEqual(results, [302, 302])
        observation.refresh_from_db()
        self.assertEqual((observation.status, observation.value, observation.submitted_by_id, observation.line_reviewed_by_id),
                         ("approved", Decimal("120.000"), resident.pk, senior.pk))
        self.assertIn(observation.reviewed_by_id, [actor.pk for actor in self.staff])
        self.assertIsNotNone(observation.reviewed_at)
        reading = Reading.objects.get(meter=meter, date=self.today)
        self.assertEqual((reading.value, observation.reading_id), (Decimal("120.000"), reading.pk))
        self.assertEqual(Reading.objects.filter(meter=meter).count(), 2)
        self.assertEqual(list(reading.history.values_list("value", "history_user_id")), [(Decimal("120.000"), observation.reviewed_by_id)])
        self.assertEqual(list(observation.history.order_by("history_date", "history_id").values_list("status", "history_user_id")),
                         before_history + [("approved", observation.reviewed_by_id)])
        previous.refresh_from_db()
        self.assertEqual(previous.value, Decimal("100.000"))
        self.assertEqual(previous.history.count(), 1)
