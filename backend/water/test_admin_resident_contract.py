"""Cross-surface acceptance with independent expected states and amounts (#121)."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from io import StringIO
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
import tempfile

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections
from django.db.models import Sum
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from .controller_scope import ControllerLineAccess
from .access_control import AccessAssignment
from .access_policy import ScopeType
from .appeal_workflow import close_resolved_appeal, send_board_reply
from .models import (
    Account, AppealCategory, BillingPeriod, BillingPolicy, Charge,
    ControllerReadingSubmission, Membership, Meter, Payment, PaymentAllocation,
    Person, Reading, ResidentAccess, ResidentAppeal, SupplyNode, Tariff, User, WaterGroup,
)
from .portal_permissions import PortalGrant
from .resident_models import ResidentAppealAttachment, ResidentAppealBoardMessage, ResidentAppealMessage, ResidentIdentity


class AdminResidentContractTests(TestCase):
    password = "synthetic-contract-password"

    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.today = timezone.localdate()
        self.staff = User.objects.create_user(
            username="contract-staff", password=self.password, is_staff=True,
        )
        self.staff.groups.add(Group.objects.get(name="Администратор ТСН"))
        self.resident = User.objects.create_user(username="contract-resident", password=self.password)
        self.neighbour = User.objects.create_user(username="contract-neighbour", password=self.password)
        self.stranger = User.objects.create_user(username="contract-stranger", password=self.password)
        self.account = Account.objects.create(number="CONTRACT-A", plot="Synthetic plot A")
        self.other = Account.objects.create(number="CONTRACT-B", plot="Synthetic plot B")
        for user, account in (
            (self.resident, self.account), (self.neighbour, self.account), (self.stranger, self.other),
        ):
            ResidentAccess.objects.create(
                user=user, account=account, role="owner", starts=self.today - timedelta(days=60),
            )
        self.category = AppealCategory.objects.create(name="Synthetic contract", active=True)
        self.staff_client = self._client(self.staff)
        self.resident_client = self._client(self.resident)

    def _client(self, user):
        client = Client()
        url = "/admin/login/" if user.is_staff else "/admin/cabinet/login/"
        response = client.post(url, {"username": user.username, "password": self.password})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(client.session["_auth_user_id"]), user.pk)
        return client

    def _new_appeal(self):
        response = self.resident_client.post(
            f"/admin/cabinet/account/{self.account.pk}/appeal/new/",
            {"category": self.category.pk, "subject": "Contract question", "message": "Initial question"},
        )
        self.assertEqual(response.status_code, 302)
        return ResidentAppeal.objects.get(author=self.resident)

    def test_complete_conversation_has_both_parties_and_exact_audit(self):
        appeal = self._new_appeal()
        staff_url = f"/work/appeals/{appeal.pk}/"
        resident_url = f"/admin/cabinet/account/{self.account.pk}/appeal/{appeal.pk}/"
        self.assertEqual(self.staff_client.post(staff_url, {
            "action": "reply", "body": "Please clarify", "next_status": "awaiting_resident",
        }).status_code, 302)
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, "awaiting_resident")
        listing = self.resident_client.get(f"/admin/cabinet/account/{self.account.pk}/appeals/")
        self.assertContains(listing, "Новый ответ")
        self.assertContains(self.resident_client.get(resident_url), "Please clarify", count=1)
        self.assertNotContains(
            self.resident_client.get(f"/admin/cabinet/account/{self.account.pk}/appeals/"), "Новый ответ",
        )
        self.assertEqual(self.resident_client.post(resident_url, {"body": "Clarification"}).status_code, 302)
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, "in_progress")
        self.assertContains(self.staff_client.get(staff_url), "Clarification", count=1)
        self.assertEqual(self.staff_client.post(staff_url, {
            "action": "reply", "body": "Final answer", "next_status": "resolved",
        }).status_code, 302)
        self.assertContains(self.resident_client.get(resident_url), "Final answer", count=1)
        self.assertEqual(self.staff_client.post(staff_url, {"action": "close"}).status_code, 302)
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, "closed")
        self.assertEqual(appeal.response, "Final answer")
        self.assertEqual(appeal.responded_by_id, self.staff.pk)
        self.assertIsNotNone(appeal.responded_at)
        self.assertEqual(list(appeal.history.order_by("history_date", "history_id").values_list(
            "status", "history_user_id",
        )), [
            ("new", self.resident.pk), ("awaiting_resident", self.staff.pk),
            ("in_progress", self.resident.pk), ("resolved", self.staff.pk), ("closed", self.staff.pk),
        ])
        self.assertEqual(list(appeal.board_messages.values_list("author_id", "body")), [
            (self.staff.pk, "Please clarify"),
        ])
        self.assertEqual(list(appeal.resident_messages.values_list("author_id", "body")), [
            (self.resident.pk, "Clarification"),
        ])
        history_count = appeal.history.count()
        self.resident_client.post(resident_url, {"body": "Stale reply"})
        self.staff_client.post(staff_url, {"action": "reply", "body": "Late reply", "next_status": "in_progress"})
        self.assertEqual(self.staff_client.post(staff_url, {"action": "close"}).status_code, 302)
        self.assertEqual(ResidentAppealMessage.objects.count(), 1)
        self.assertEqual(ResidentAppealBoardMessage.objects.count(), 1)
        self.assertEqual(appeal.history.count(), history_count)
        appeal.refresh_from_db()
        self.assertEqual((appeal.account_id, appeal.author_id, appeal.message, appeal.status, appeal.response, appeal.responded_by_id),
                         (self.account.pk, self.resident.pk, "Initial question", "closed", "Final answer", self.staff.pk))

    def test_shared_account_does_not_share_private_conversation(self):
        appeal = self._new_appeal()
        url = f"/admin/cabinet/account/{self.account.pk}/appeal/{appeal.pk}/"
        for user in (self.neighbour, self.stranger):
            client = self._client(user)
            self.assertEqual(client.get(url).status_code, 404)
            self.assertEqual(client.post(url, {"body": "Foreign reply"}).status_code, 404)
        self.assertFalse(ResidentAppealMessage.objects.exists())
        self.assertEqual(appeal.history.count(), 1)

    def test_granular_grant_without_legacy_access_can_complete_conversation(self):
        person = Person.objects.create(full_name="Synthetic granular resident")
        user = User.objects.create_user(username="contract-granular", password=self.password)
        ResidentIdentity.objects.create(user=user, person=person, verified_by=self.staff, basis="Synthetic identity")
        grant = PortalGrant.objects.create(
            person=person, account=self.account, starts=self.today - timedelta(days=1),
            can_view_account=True, can_use_appeals=True, basis="Synthetic appeal grant", verified_by=self.staff,
        )
        self.assertFalse(ResidentAccess.objects.filter(user=user).exists())
        client = self._client(user)
        response = client.post(f"/admin/cabinet/account/{self.account.pk}/appeal/new/", {
            "category": self.category.pk, "subject": "Granular question", "message": "Granted question",
        })
        self.assertEqual(response.status_code, 302)
        appeal = ResidentAppeal.objects.get(author=user)
        staff_url = f"/work/appeals/{appeal.pk}/"
        self.assertEqual(self.staff_client.post(staff_url, {
            "action": "reply", "body": "Granted answer", "next_status": "awaiting_resident",
        }).status_code, 302)
        resident_url = f"/admin/cabinet/account/{self.account.pk}/appeal/{appeal.pk}/"
        self.assertContains(client.get(resident_url), "Granted answer")
        grant.ends = self.today
        grant._history_user = self.staff
        grant._change_reason = "End synthetic granular grant"
        grant.save()
        self.assertEqual(client.get(resident_url).status_code, 404)
        self.assertEqual(client.post(resident_url, {"body": "After expiry"}).status_code, 404)
        # Staff must still be able to finish a historical appeal after access ends.
        self.assertEqual(self.staff_client.post(staff_url, {
            "action": "reply", "body": "Historical resolution", "next_status": "resolved",
        }).status_code, 302)
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, "resolved")

    def test_access_ending_rejects_open_session_and_stale_form(self):
        appeal = self._new_appeal()
        url = f"/admin/cabinet/account/{self.account.pk}/appeal/{appeal.pk}/"
        self.assertEqual(self.resident_client.get(url).status_code, 200)
        access = ResidentAccess.objects.get(user=self.resident)
        access.ends = self.today
        access._history_user = self.staff
        access._change_reason = "End synthetic access"
        access.save()
        self.assertEqual(self.resident_client.get(url).status_code, 404)
        self.assertEqual(self.resident_client.post(url, {"body": "Old open form"}).status_code, 404)
        self.assertFalse(ResidentAppealMessage.objects.exists())

    def test_dual_role_routes_keep_personal_account_boundary(self):
        person = Person.objects.create(full_name="Synthetic dual role")
        ResidentIdentity.objects.create(user=self.staff, person=person, verified_by=self.staff, basis="Synthetic identity")
        PortalGrant.objects.create(
            person=person, account=self.account, starts=self.today - timedelta(days=1),
            can_view_account=True, can_use_appeals=True, basis="Synthetic personal grant", verified_by=self.staff,
        )
        self.assertEqual(self.staff_client.get("/work/appeals/").status_code, 200)
        self.assertEqual(self.staff_client.get(f"/admin/cabinet/account/{self.account.pk}/").status_code, 200)
        self.assertEqual(self.staff_client.get(f"/admin/cabinet/account/{self.other.pk}/").status_code, 404)
        self.assertEqual(self.resident_client.get("/work/appeals/").status_code, 302)

    def test_scoped_staff_cannot_read_reply_close_or_download_foreign_appeal(self):
        own = self._new_appeal()
        foreign = ResidentAppeal.objects.create(
            account=self.other, author=self.stranger, category=self.category,
            subject="Foreign private subject", message="Foreign private message",
            status="resolved", response="Foreign final response",
        )
        ResidentAppeal.objects.create(
            account=self.other, author=self.stranger, category=self.category,
            subject="Another foreign open appeal", message="Foreign question",
        )
        user = User.objects.create_user(username="contract-scoped", password=self.password, is_staff=True)
        person = Person.objects.create(full_name="Synthetic scoped staff")
        ResidentIdentity.objects.create(user=user, person=person, verified_by=self.staff, basis="Synthetic identity")
        capabilities = ["appeals.view", "appeals.reply", "appeals.close", "appeals.status.change", "appeals.attachment.view"]
        AccessAssignment.objects.create(
            person=person, role_code="scoped_appeals", role_label="Synthetic scoped appeals",
            allowed_capabilities=capabilities, capabilities=capabilities,
            scope_type=ScopeType.ACCOUNT.value, scope_object_id=self.account.pk,
            starts=self.today - timedelta(days=1), basis="Synthetic scope", granted_by=self.staff,
        )
        client = self._client(user)
        listing = client.get("/work/appeals/?state=all")
        self.assertContains(listing, own.subject)
        self.assertNotContains(listing, foreign.subject)
        self.assertEqual(listing.context["counts"]["total"], 1)
        attention = client.get("/work/").context["attention"]
        self.assertEqual([row["count"] for row in attention if row["label"] == "Открытые обращения"], [1])
        AccessAssignment.objects.create(
            person=person, role_code="all_accounts_view", role_label="Synthetic all accounts view",
            allowed_capabilities=["accounts.view"], capabilities=["accounts.view"],
            scope_type=ScopeType.ALL.value, starts=self.today - timedelta(days=1),
            basis="Synthetic distinct scope", granted_by=self.staff,
        )
        foreign_card = client.get(f"/work/accounts/{self.other.pk}/")
        self.assertNotIn("open_appeals", foreign_card.context)
        self.assertNotContains(foreign_card, "Открытые обращения")
        self.assertEqual(client.get(f"/work/appeals/{foreign.pk}/").status_code, 404)
        self.assertEqual(client.post(f"/work/appeals/{foreign.pk}/", {
            "action": "close",
        }).status_code, 404)
        history_count = foreign.history.count()
        with self.assertRaises(PermissionDenied):
            send_board_reply(appeal_id=foreign.pk, actor=user, body="Forbidden", next_status="resolved")
        with self.assertRaises(PermissionDenied):
            close_resolved_appeal(appeal_id=foreign.pk, actor=user)
        self.assertEqual(foreign.history.count(), history_count)
        self.assertFalse(foreign.board_messages.exists())
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            attachment = ResidentAppealAttachment.objects.create(
                appeal=foreign, uploaded_by=self.stranger,
                document=SimpleUploadedFile("synthetic.pdf", b"%PDF-1.4\nSynthetic test only"),
            )
            self.assertEqual(client.get(reverse("staff_workspace:appeal_attachment", args=[attachment.pk])).status_code, 404)
            self.assertEqual(client.get(reverse("admin_appeal_attachment_download", args=[attachment.pk])).status_code, 404)
            own_attachment = ResidentAppealAttachment.objects.create(
                appeal=own, uploaded_by=self.resident,
                document=SimpleUploadedFile("own.pdf", b"%PDF-1.4\nOwn synthetic content"),
            )
            download = client.get(reverse("staff_workspace:appeal_attachment", args=[own_attachment.pk]))
            self.assertEqual(download.status_code, 200)
            # Client's streaming wrapper closes the response with DB signals
            # isolated. A second manual close would close TestCase's transaction.
            self.assertEqual(b"".join(download.streaming_content), b"%PDF-1.4\nOwn synthetic content")
        self.assertEqual(client.get(reverse("admin_appeal_attachments", args=[foreign.pk])).status_code, 404)
        # An unrelated view scope must not authorize reply on an allowed view.
        assignment = AccessAssignment.objects.get(person=person, role_code="scoped_appeals")
        assignment.capabilities = ["appeals.view"]
        assignment.save()
        self.assertEqual(client.post(f"/work/appeals/{own.pk}/", {
            "action": "reply", "body": "Forbidden", "next_status": "in_progress",
        }).status_code, 403)
        assignment.ends = self.today
        assignment.save()
        self.assertEqual(client.get(f"/work/appeals/{own.pk}/").status_code, 403)
        self.assertEqual(client.post(f"/work/appeals/{own.pk}/", {
            "action": "reply", "body": "Expired open form", "next_status": "in_progress",
        }).status_code, 403)

    def test_granular_view_does_not_authorize_appeal_or_foreign_reattribution(self):
        person = Person.objects.create(full_name="Synthetic view-only resident")
        user = User.objects.create_user(username="contract-view-only", password=self.password)
        ResidentIdentity.objects.create(user=user, person=person, verified_by=self.staff, basis="Synthetic identity")
        PortalGrant.objects.create(
            person=person, account=self.account, starts=self.today - timedelta(days=1),
            can_view_account=True, basis="View only", verified_by=self.staff,
        )
        client = self._client(user)
        self.assertEqual(client.post(f"/admin/cabinet/account/{self.account.pk}/appeal/new/", {
            "category": self.category.pk, "subject": "Forbidden", "message": "Forbidden",
        }).status_code, 404)
        with self.assertRaises(ValidationError):
            ResidentAppeal.objects.create(account=self.account, author=user, category=self.category, subject="Forbidden", message="Forbidden")
        appeal = self._new_appeal()
        appeal.account = self.other
        with self.assertRaises(ValidationError):
            appeal.save()
        appeal.refresh_from_db()
        self.assertEqual(appeal.account_id, self.account.pk)

    def test_failed_staff_transition_rolls_back_message_and_history(self):
        appeal = self._new_appeal()
        history_count = appeal.history.count()
        with patch.object(ResidentAppeal, "save", side_effect=RuntimeError("Synthetic storage failure")):
            with self.assertRaisesRegex(RuntimeError, "Synthetic storage failure"):
                send_board_reply(appeal_id=appeal.pk, actor=self.staff, body="Rolled back", next_status="awaiting_resident")
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, "new")
        self.assertFalse(appeal.board_messages.exists())
        self.assertEqual(appeal.history.count(), history_count)

    def test_disabled_author_does_not_strand_staff_resolution(self):
        appeal = self._new_appeal()
        self.resident.is_active = False
        self.resident.save()
        self.assertEqual(self.resident_client.get(f"/admin/cabinet/account/{self.account.pk}/appeal/{appeal.pk}/").status_code, 302)
        self.assertEqual(self.staff_client.post(f"/work/appeals/{appeal.pk}/", {
            "action": "reply", "body": "Resolved after account disabled", "next_status": "resolved",
        }).status_code, 302)
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, "resolved")
        self.assertEqual(appeal.author_id, self.resident.pk)

    def test_real_resident_login_preserves_csrf_denial(self):
        client = Client(enforce_csrf_checks=True)
        login_url = "/admin/cabinet/login/"
        client.get(login_url)
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.post(login_url, {
            "username": self.resident.username, "password": self.password, "csrfmiddlewaretoken": token,
        }).status_code, 302)
        self.assertEqual(client.post(f"/admin/cabinet/account/{self.account.pk}/appeal/new/", {
            "category": self.category.pk, "subject": "No CSRF", "message": "Forbidden",
        }).status_code, 403)
        self.assertFalse(ResidentAppeal.objects.filter(subject="No CSRF").exists())

    def test_water_submission_review_finalization_and_resident_result(self):
        node = SupplyNode.objects.create(name="Contract node")
        line = WaterGroup.objects.create(name="Contract line", node=node)
        Membership.objects.create(account=self.account, group=line, starts=self.today - timedelta(days=60))
        meter = Meter.objects.create(serial="CONTRACT-METER", kind="individual", node=node, account=self.account)
        Reading.objects.create(meter=meter, date=self.today - timedelta(days=30), value=Decimal("100.000"))
        senior = User.objects.create_user(username="contract-senior", password=self.password, is_staff=True)
        senior.user_permissions.add(Permission.objects.get(
            content_type__app_label="water", codename="use_controller_workspace",
        ))
        ControllerLineAccess.objects.create(user=senior, group=line, starts=self.today - timedelta(days=60))
        senior_client = self._client(senior)
        post_url = f"/admin/cabinet/account/{self.account.pk}/meter/{meter.pk}/reading/"
        for value in ("119.000", "120.000"):
            self.assertEqual(self.resident_client.post(post_url, {
                "date": self.today.isoformat(), "value": value, "notes": "Synthetic observation",
            }).status_code, 302)
        observation = ControllerReadingSubmission.objects.get(meter=meter)
        self.assertEqual(observation.value, Decimal("120.000"))
        self.assertEqual(observation.status, "pending")
        self.assertFalse(Reading.objects.filter(meter=meter, date=self.today).exists())
        self.assertEqual(senior_client.post("/work/water/line/", {
            "date": self.today.isoformat(), "review_submission": observation.pk,
            "decision": "flag", "line_review_comment": "Checked independently",
        }).status_code, 302)
        observation.refresh_from_db()
        self.assertEqual(observation.line_review_status, "flagged")
        approve_url = f"/admin/water/controllerreadingsubmission/{observation.pk}/approve/"
        self.assertEqual(self.staff_client.post(approve_url).status_code, 302)
        self.staff_client.post(approve_url)
        observation.refresh_from_db()
        self.assertEqual(observation.status, "approved")
        self.assertEqual(Reading.objects.filter(meter=meter, date=self.today).count(), 1)
        self.assertEqual(observation.reading.value, Decimal("120.000"))
        view = self.resident_client.get(f"/admin/cabinet/account/{self.account.pk}/water/")
        self.assertEqual(view.context["meter_rows"][0]["latest"].value, Decimal("120.000"))
        self.assertEqual(Reading.objects.get(meter=meter, date=self.today - timedelta(days=30)).value, Decimal("100.000"))

    def test_finance_partial_payment_repeat_and_reversal_oracle(self):
        period = BillingPeriod.objects.create(starts=self.today - timedelta(days=30), ends=self.today, status="open")
        BillingPolicy.objects.create(
            name="Contract norm", is_default=True, missing_reading="norm",
            monthly_norm_m3=Decimal("20.000"), payment_allocation="oldest",
        )
        Tariff.objects.create(name="Contract tariff", rate=Decimal("10.0000"), starts=period.starts)
        period_url = f"/work/finance/periods/{period.pk}/"
        self.assertEqual(self.staff_client.post(period_url, {"action": "calculate"}).status_code, 302)
        charge = Charge.objects.get(account=self.account, period=period)
        self.assertEqual(charge.amount, Decimal("200.00"))
        self.assertEqual(charge.status, "draft")
        resident_url = f"/admin/cabinet/account/{self.account.pk}/payments/"
        self.assertEqual(self.resident_client.get(resident_url).context["totals"]["balance"], Decimal("0.00"))
        self.assertEqual(self.staff_client.post(period_url, {"action": "approve_charge", "charge_id": charge.pk}).status_code, 302)
        self.assertEqual(self.resident_client.get(resident_url).context["totals"]["balance"], Decimal("200.00"))
        self.assertEqual(self.staff_client.post("/work/finance/payments/new/", {
            "account": self.account.pk, "paid_on": self.today.isoformat(), "amount": "150.00",
            "method": "bank", "reference": "CONTRACT-150", "notes": "Synthetic payment",
        }).status_code, 302)
        payment = Payment.objects.get(account=self.account)
        self.assertEqual(payment.status, "pending")
        self.assertEqual(self.resident_client.get(resident_url).context["totals"]["balance"], Decimal("200.00"))
        payment_url = f"/work/finance/payments/{payment.pk}/"
        for action in ("confirm", "allocate", "confirm", "allocate"):
            self.assertEqual(self.staff_client.post(payment_url, {"action": action}).status_code, 302)
        allocation = PaymentAllocation.objects.get(payment=payment)
        self.assertEqual(allocation.charge_id, charge.pk)
        self.assertEqual(allocation.amount, Decimal("150.00"))
        self.assertEqual(PaymentAllocation.objects.filter(charge=charge, payment__status="confirmed").aggregate(
            total=Sum("amount"),
        )["total"], Decimal("150.00"))
        self.assertEqual(self.resident_client.get(resident_url).context["totals"]["balance"], Decimal("50.00"))
        self.assertEqual(self._client(self.stranger).get(resident_url).status_code, 404)
        counts = (Charge.objects.count(), Payment.objects.count(), PaymentAllocation.objects.count())
        payment.refresh_from_db()
        history_count = payment.history.count()
        self.assertEqual(self.resident_client.post(payment_url, {"action": "reverse"}).status_code, 302)
        self.assertEqual((Charge.objects.count(), Payment.objects.count(), PaymentAllocation.objects.count()), counts)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "confirmed")
        self.assertEqual(payment.history.count(), history_count)
        self.assertEqual(self.staff_client.post(payment_url, {"action": "reverse"}).status_code, 302)
        payment.refresh_from_db()
        self.assertEqual(payment.status, "reversed")
        self.assertEqual(PaymentAllocation.objects.get(pk=allocation.pk).amount, Decimal("150.00"))
        self.assertEqual(self.resident_client.get(resident_url).context["totals"]["balance"], Decimal("200.00"))


@skipUnless(connection.vendor == "postgresql", "Concurrent row-lock contract requires PostgreSQL")
class AppealConcurrencyContractTests(TransactionTestCase):
    def test_two_staff_closures_commit_one_terminal_history_event(self):
        call_command("setup_roles", stdout=StringIO())
        staff = User.objects.create_user(username="race-staff", is_staff=True)
        staff.groups.add(Group.objects.get(name="Администратор ТСН"))
        resident = User.objects.create_user(username="race-resident")
        account = Account.objects.create(number="RACE-A")
        ResidentAccess.objects.create(user=resident, account=account, role="owner", starts=timezone.localdate() - timedelta(days=1))
        appeal = ResidentAppeal.objects.create(
            account=account, author=resident, category=AppealCategory.objects.create(name="Race category"),
            subject="Concurrent closure", message="Synthetic question", status="resolved", response="Synthetic final answer",
        )
        initial_history = appeal.history.count()
        barrier = Barrier(2, timeout=15)

        def close():
            try:
                actor = User.objects.get(pk=staff.pk)
                barrier.wait()
                return close_resolved_appeal(appeal_id=appeal.pk, actor=actor).status
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(close) for _ in range(2)]
            self.assertEqual([future.result(timeout=30) for future in futures], ["closed", "closed"])
        appeal.refresh_from_db()
        self.assertEqual(appeal.status, "closed")
        self.assertEqual(appeal.history.count(), initial_history + 1)
        self.assertEqual(appeal.history.filter(status="closed", history_user_id=staff.pk).count(), 1)
        self.assertFalse(appeal.board_messages.exists())
