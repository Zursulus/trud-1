from datetime import timedelta
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from .access_control import AccessAssignment
from .access_policy import ScopeType
from .models import Account, BillingPeriod, Charge, Person, User
from .resident_models import ResidentIdentity


class VtbAccountScopeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="vtb-scope-admin", is_staff=True, is_superuser=True,
        )
        self.staff = User.objects.create_user(username="vtb-scope-staff", is_staff=True)
        self.person = Person.objects.create(full_name="Synthetic VTB scoped staff")
        ResidentIdentity.objects.create(
            user=self.staff, person=self.person, verified_by=self.admin, basis="Synthetic VTB scope",
        )
        self.allowed = Account.objects.create(
            number="VTB-SCOPE-A", contact_name="Allowed Person", plot="Allowed address",
        )
        self.hidden = Account.objects.create(
            number="VTB-SCOPE-B", contact_name="Hidden Person", plot="Hidden address",
        )
        AccessAssignment.objects.create(
            person=self.person,
            role_code="synthetic_vtb_account",
            role_label="Synthetic VTB A only",
            allowed_capabilities=["finance.view", "finance.export"],
            capabilities=["finance.view", "finance.export"],
            scope_type=ScopeType.ACCOUNT.value,
            scope_object_id=self.allowed.pk,
            starts=timezone.localdate() - timedelta(days=1),
            basis="Synthetic bounded VTB authority",
            granted_by=self.admin,
        )
        period = BillingPeriod.objects.create(
            starts=timezone.localdate() - timedelta(days=30),
            ends=timezone.localdate(),
        )
        Charge.objects.create(
            account=self.allowed, period=period, kind="service",
            amount=Decimal("10.00"), status="approved",
        )
        Charge.objects.create(
            account=self.hidden, period=period, kind="service",
            amount=Decimal("900.00"), status="approved",
        )
        self.client.force_login(self.staff)

    def _payment_file(self, account_number):
        text = (
            f"27-01-2020;16-07-15;4;427285406;abc123;{account_number};Test Payer;"
            "Test address;0120;10.00;10.00;0.00\r\n"
            "=1;10.00;10.00;0.00;8;28-01-2020"
        )
        return SimpleUploadedFile(
            "payments.txt", text.encode("cp1251"), content_type="text/plain",
        )

    def test_debt_preview_and_download_do_not_escape_account_scope(self):
        preview = self.client.get("/work/finance/vtb/debt/")
        self.assertEqual(preview.status_code, 200)
        self.assertEqual([row.account_number for row in preview.context["rows"]], [self.allowed.number])
        self.assertContains(preview, self.allowed.number)
        self.assertNotContains(preview, self.hidden.number)
        self.assertNotContains(preview, self.hidden.contact_name)

        download = self.client.get("/work/finance/vtb/debt/?download=1")
        self.assertEqual(download.status_code, 200)
        payload = download.content.decode("cp1251")
        self.assertIn(self.allowed.number, payload)
        self.assertNotIn(self.hidden.number, payload)
        self.assertNotIn(self.hidden.contact_name, payload)

    def test_payment_dry_run_does_not_resolve_hidden_account(self):
        response = self.client.post(
            "/work/finance/vtb/payments/dry-run/",
            {"encoding": "cp1251", "registry": self._payment_file(self.hidden.number)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Не найдено:</strong> 1")
        self.assertContains(response, self.hidden.number)
        self.assertIsNone(response.context["report"]["rows"][0]["account"])
