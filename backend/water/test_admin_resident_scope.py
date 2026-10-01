"""Pure V2 scopes: no global permissions masking account boundary failures."""
from datetime import timedelta
from decimal import Decimal
from io import StringIO
import tempfile

from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .access_control import AccessAssignment
from .access_policy import ScopeType
from .access_workflow import end_portal_grant, update_portal_grant_rights
from .document_workflow import create_account_document, update_account_document
from .finance_workflow import approve_charge, cancel_charge, create_payment, confirm_payment, reverse_payment, allocate_confirmed_payment
from .models import Account, AccountDocument, BillingPeriod, BillingPolicy, Charge, DocumentCategory, Payment, PaymentAllocation, Person, User
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity
from public_site.models import PublicNews


class ServiceAccountScopeTests(TestCase):
    password = "synthetic-scope-password"

    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.today = timezone.localdate()
        cls.admin = User.objects.create_user(username="scope-global", password=cls.password, is_staff=True)
        cls.admin.groups.add(Group.objects.get(name="Администратор ТСН"))
        cls.user = User.objects.create_user(username="scope-pure-v2", password=cls.password, is_staff=True)
        cls.person = Person.objects.create(full_name="Synthetic scoped actor")
        ResidentIdentity.objects.create(user=cls.user, person=cls.person, verified_by=cls.admin, basis="Synthetic identity")
        cls.a = Account.objects.create(number="SCOPE-A", plot="Synthetic A")
        cls.b = Account.objects.create(number="SCOPE-B", plot="Synthetic B")
        cls.category = DocumentCategory.objects.create(name="Synthetic scope document")
        caps = ["finance.view", "finance.charge.approve", "finance.charge.cancel", "finance.payment.create",
                "finance.payment.confirm", "finance.payment.reverse", "finance.payment.allocate",
                "documents.account.view", "documents.account.create", "documents.account.edit_metadata", "documents.account.download",
                "access.view", "access.grant.view", "access.grant.edit", "access.grant.end"]
        AccessAssignment.objects.create(
            person=cls.person, role_code="synthetic_account_services", role_label="Synthetic A-only services",
            allowed_capabilities=caps, capabilities=caps, scope_type=ScopeType.ACCOUNT.value, scope_object_id=cls.a.pk,
            starts=cls.today - timedelta(days=1), basis="Synthetic bounded authority", granted_by=cls.admin,
        )
        AccessAssignment.objects.create(
            person=cls.person, role_code="synthetic_all_account_directory", role_label="Directory only",
            allowed_capabilities=["accounts.view"], capabilities=["accounts.view"], scope_type=ScopeType.ALL.value,
            starts=cls.today - timedelta(days=1), basis="Synthetic separate scope", granted_by=cls.admin,
        )

    def setUp(self):
        response = self.client.post("/admin/login/", {"username": self.user.username, "password": self.password})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.user.groups.exists())
        self.assertFalse(self.user.user_permissions.exists())

    def test_finance_lists_forms_actions_and_directory_do_not_escape_account(self):
        period = BillingPeriod.objects.create(starts=self.today - timedelta(days=30), ends=self.today, status="calculated")
        BillingPolicy.objects.create(name="Synthetic allocation", is_default=True, payment_allocation="oldest")
        charges = [Charge.objects.create(account=a, period=period, kind="service", amount=Decimal(amount), status="draft")
                   for a, amount in [(self.a, "100.00"), (self.b, "900.00")]]
        payments = [Payment.objects.create(account=a, paid_on=self.today, amount=Decimal(amount), method="bank", reference=a.number, status="pending")
                    for a, amount in [(self.a, "10.00"), (self.b, "90.00")]]
        dashboard = self.client.get("/work/finance/")
        self.assertEqual(dashboard.context["draft_charge_count"], 1)
        self.assertEqual(dashboard.context["draft_charge_amount"], Decimal("100.00"))
        self.assertEqual(dashboard.context["pending_payment_amount"], Decimal("10.00"))
        self.assertNotContains(dashboard, self.b.number)
        listing = self.client.get("/work/finance/payments/?state=all")
        self.assertEqual(list(listing.context["page"].object_list.values_list("pk", flat=True)), [payments[0].pk])
        detail = self.client.get(f"/work/finance/periods/{period.pk}/")
        self.assertEqual([c.pk for c in detail.context["charges"]], [charges[0].pk])
        self.assertEqual(detail.context["draft_amount"], Decimal("100.00"))
        self.assertEqual(self.client.get(f"/work/finance/accounts/{self.b.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"/work/finance/payments/{payments[1].pk}/").status_code, 404)
        self.assertNotIn("finance", self.client.get(f"/work/accounts/{self.b.pk}/").context)
        form = self.client.get("/work/finance/payments/new/").context["form"]
        self.assertEqual(list(form.fields["account"].queryset.values_list("pk", flat=True)), [self.a.pk])
        forged = self.client.post("/work/finance/payments/new/", {
            "account": self.b.pk, "paid_on": self.today.isoformat(), "amount": "30.00", "method": "bank",
        })
        self.assertEqual(forged.status_code, 200)
        self.assertIn("account", forged.context["form"].errors)
        foreign_history = (charges[1].history.count(), payments[1].history.count())
        operations = [
            lambda: approve_charge(charge_id=charges[1].pk, actor=self.user),
            lambda: cancel_charge(charge_id=charges[1].pk, actor=self.user),
            lambda: create_payment(actor=self.user, account=self.b, paid_on=self.today, amount=Decimal("30.00"), method="bank"),
            lambda: confirm_payment(payment_id=payments[1].pk, actor=self.user),
            lambda: reverse_payment(payment_id=payments[1].pk, actor=self.user),
            lambda: allocate_confirmed_payment(payment_id=payments[1].pk, actor=self.user),
        ]
        for operation in operations:
            with self.assertRaises(PermissionDenied):
                operation()
        self.assertEqual((charges[1].history.count(), payments[1].history.count()), foreign_history)
        self.assertEqual(Payment.objects.count(), 2)
        self.assertFalse(PaymentAllocation.objects.exists())
        approve_charge(charge_id=charges[0].pk, actor=self.user)
        confirm_payment(payment_id=payments[0].pk, actor=self.user)
        allocations, _ = allocate_confirmed_payment(payment_id=payments[0].pk, actor=self.user)
        self.assertEqual(sum((x.amount for x in allocations), Decimal("0.00")), Decimal("10.00"))

    def test_document_scope_applies_before_file_open_and_metadata_mutation(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            documents = [AccountDocument.objects.create(
                account=a, category=self.category, title=f"Private {a.number}",
                document=SimpleUploadedFile(f"{a.number}.pdf", b"%PDF-1.4\nSynthetic private content"),
                published_at=timezone.now() - timedelta(minutes=1), visible_to_residents=True,
            ) for a in (self.a, self.b)]
            listing = self.client.get("/work/documents/")
            self.assertEqual(listing.context["account_document_count"], 1)
            self.assertNotContains(listing, documents[1].title)
            self.assertEqual(self.client.get(reverse("staff_workspace:account_document", args=[documents[1].pk])).status_code, 404)
            self.assertEqual(self.client.get(reverse("staff_workspace:account_document_download", args=[documents[1].pk])).status_code, 404)
            self.assertNotIn("documents_count", self.client.get(f"/work/accounts/{self.b.pk}/").context)
            form = self.client.get("/work/documents/accounts/new/").context["form"]
            self.assertEqual(list(form.fields["account"].queryset.values_list("pk", flat=True)), [self.a.pk])
            with self.assertRaises(PermissionDenied):
                create_account_document(account=self.b, category=self.category, title="Forbidden", document=SimpleUploadedFile("forbidden.pdf", b"%PDF"),
                                        published_at=timezone.now(), visible_to_residents=True, notes="", actor=self.user)
            history_count = documents[1].history.count()
            with self.assertRaises(PermissionDenied):
                update_account_document(documents[1].pk, category=self.category, title="Forbidden", published_at=timezone.now(),
                                        visible_to_residents=False, notes="", change_reason="Forbidden", actor=self.user)
            self.assertEqual(documents[1].history.count(), history_count)
            download = self.client.get(reverse("staff_workspace:account_document_download", args=[documents[0].pk]))
            self.assertEqual(download.status_code, 200)
            self.assertEqual(b"".join(download.streaming_content), b"%PDF-1.4\nSynthetic private content")

    def test_access_global_center_and_services_fail_closed_for_scoped_actor(self):
        grant = PortalGrant.objects.create(person=self.person, account=self.b, starts=self.today - timedelta(days=1),
                                           can_view_account=True, basis="Synthetic foreign grant", verified_by=self.admin)
        self.assertEqual(self.client.get("/work/access/").status_code, 403)
        self.assertEqual(self.client.get(f"/work/access/grants/{grant.pk}/").status_code, 403)
        self.assertEqual(self.client.post(f"/work/access/grants/{grant.pk}/", {"action": "end", "ends_on": self.today.isoformat()}).status_code, 403)
        history_count = grant.history.count()
        with self.assertRaises(PermissionDenied):
            end_portal_grant(grant.pk, ends_on=self.today, actor=self.user)
        with self.assertRaises(PermissionDenied):
            update_portal_grant_rights(grant.pk, actor=self.user, can_view_account=False, can_view_finance=False,
                                      can_submit_water=False, can_view_documents=False, can_use_appeals=False, can_represent=False)
        grant.refresh_from_db()
        self.assertIsNone(grant.ends)
        self.assertTrue(grant.can_view_account)
        self.assertEqual(grant.history.count(), history_count)

    def test_staff_publication_resident_download_attention_and_unpublication(self):
        resident = User.objects.create_user(username="publication-resident", password=self.password)
        person = Person.objects.create(full_name="Synthetic publication recipient")
        ResidentIdentity.objects.create(user=resident, person=person, verified_by=self.admin, basis="Synthetic identity")
        PortalGrant.objects.create(person=person, account=self.a, starts=self.today - timedelta(days=1),
                                   can_view_account=True, can_view_documents=True, basis="Documents only", verified_by=self.admin)
        staff, reader = Client(), Client()
        self.assertEqual(staff.post("/admin/login/", {"username": self.admin.username, "password": self.password}).status_code, 302)
        self.assertEqual(reader.post("/admin/cabinet/login/", {"username": resident.username, "password": self.password}).status_code, 302)
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            publication_date = timezone.localtime(timezone.now() - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M")
            self.assertEqual(staff.post("/work/documents/accounts/new/", {
                "account": self.a.pk, "category": self.category.pk, "title": "Published contract document",
                "document": SimpleUploadedFile("contract.pdf", b"%PDF-1.4\nExact synthetic document"),
                "published_at": publication_date, "visible_to_residents": "on",
            }).status_code, 302)
            item = AccountDocument.objects.get(title="Published contract document")
            original_file = item.document.name
            resident_url = reverse("resident_document", args=[self.a.pk, item.pk])
            download = reader.get(resident_url)
            self.assertEqual(download.status_code, 200)
            self.assertEqual(b"".join(download.streaming_content), b"%PDF-1.4\nExact synthetic document")
            self.assertIn("private", download["Cache-Control"])
            home_url = f"/admin/cabinet/account/{self.a.pk}/"
            self.assertEqual([x["kind"] for x in reader.get(home_url).context["attention"]], ["document"])
            self.assertEqual(staff.post(reverse("staff_workspace:account_document", args=[item.pk]), {
                "category": self.category.pk, "title": item.title, "published_at": publication_date,
                "change_reason": "Withdraw synthetic publication",
            }).status_code, 302)
            item.refresh_from_db()
            self.assertEqual(item.document.name, original_file)
            self.assertFalse(item.visible_to_residents)
            self.assertEqual(item.history.count(), 2)
            self.assertEqual(item.history.latest().history_user_id, self.admin.pk)
            self.assertEqual(reader.get(resident_url).status_code, 404)
            self.assertEqual(reader.get(home_url).context["attention"], [])
        payload = {"title": "Featured synthetic news", "category": "НОВОСТЬ", "summary": "Synthetic summary", "body": "Synthetic body",
                   "published_on": self.today.isoformat(), "is_featured": "on", "is_published": "on", "confirm_publication": "on"}
        self.assertEqual(staff.post("/work/documents/news/new/", payload).status_code, 302)
        news = PublicNews.objects.get(title=payload["title"])
        self.assertEqual(news.published_by_id, self.admin.pk)
        self.assertEqual([(x["kind"], x["title"]) for x in reader.get(home_url).context["attention"]], [("news", payload["title"])])
        unpublished = {k: v for k, v in payload.items() if k not in {"is_published", "confirm_publication"}}
        self.assertEqual(staff.post(f"/work/documents/news/{news.pk}/", {**unpublished, "change_reason": "Withdraw synthetic news"}).status_code, 302)
        self.assertEqual(reader.get(home_url).context["attention"], [])

    def test_granular_invite_activation_real_login_and_rights_removal(self):
        self.admin.groups.add(Group.objects.get(name="Закрытый реестр членов ТСН"))
        person = Person.objects.create(full_name="Synthetic invite recipient", email="contract-invite@example.test")
        staff, resident = Client(), Client()
        self.assertEqual(staff.post("/admin/login/", {"username": self.admin.username, "password": self.password}).status_code, 302)
        issued = staff.post("/work/access/invite/", {
            "person": person.pk, "account": self.a.pk, "email": person.email, "basis": "Synthetic verified invitation",
            "can_view_account": "on", "can_use_appeals": "on",
        })
        self.assertEqual(issued.status_code, 200)
        invite_url = issued.context["invite_url"]
        self.assertTrue(invite_url)
        self.assertFalse(PortalGrant.objects.filter(person=person).exists())
        self.assertEqual(resident.post(invite_url, {"password1": self.password, "password2": self.password}).status_code, 302)
        user = User.objects.get(email=person.email)
        self.assertEqual(ResidentIdentity.objects.get(user=user).person_id, person.pk)
        grant = PortalGrant.objects.get(person=person, account=self.a)
        self.assertTrue(grant.can_use_appeals)
        resident.post("/admin/cabinet/logout/")
        self.assertEqual(resident.post("/admin/cabinet/login/", {"username": user.username, "password": self.password}).status_code, 302)
        home_url = f"/admin/cabinet/account/{self.a.pk}/"
        self.assertEqual(resident.get(home_url).status_code, 200)
        self.assertEqual(resident.post(invite_url, {"password1": self.password, "password2": self.password}).status_code, 410)
        self.assertEqual(PortalGrant.objects.filter(person=person, account=self.a).count(), 1)
        self.assertEqual(staff.post(f"/work/access/grants/{grant.pk}/", {"action": "rights"}).status_code, 302)
        grant.refresh_from_db()
        self.assertFalse(grant.can_view_account)
        self.assertFalse(grant.can_use_appeals)
        self.assertEqual(resident.get(home_url).status_code, 404)
        self.assertEqual(grant.history.latest().history_user_id, self.admin.pk)
