from datetime import date
from decimal import Decimal

from django.test import TestCase

from .models import Account, BillingPeriod, Charge, Payment, PaymentAllocation, User
from .vtb_debt_export import build_vtb_debt_export, render_vtb_debt_registry


class VtbDebtExportTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(username="vtb-export-admin", is_staff=True, is_superuser=True)
        self.period = BillingPeriod.objects.create(starts=date(2026, 9, 1), ends=date(2026, 10, 1))
        self.account = Account.objects.create(
            number="0030142923", contact_name="Иванов Иван Иванович", plot="г.Новый, ул. Новая, дом 1",
        )
        self.charge = Charge.objects.create(
            account=self.account, period=self.period, kind="service", amount=Decimal("715.20"), status="approved",
        )

    def test_real_template_shape_and_cp1251(self):
        rows, skipped = build_vtb_debt_export(actor=self.actor)
        self.assertEqual(skipped, [])
        self.assertEqual(rows[0].period, "0926")
        self.assertEqual(rows[0].amount, Decimal("715.20"))
        payload = render_vtb_debt_registry(rows)
        self.assertEqual(
            payload.decode("cp1251"),
            "0030142923;Иванов Иван Иванович;г.Новый, ул. Новая, дом 1;0926;715.20",
        )

    def test_confirmed_allocation_reduces_exported_debt(self):
        payment = Payment.objects.create(
            account=self.account, paid_on=date(2026, 9, 15), amount=Decimal("200.00"),
            method="bank", status="confirmed",
        )
        PaymentAllocation.objects.create(payment=payment, charge=self.charge, amount=Decimal("200.00"))
        rows, _ = build_vtb_debt_export(actor=self.actor)
        self.assertEqual(rows[0].amount, Decimal("515.20"))

    def test_missing_contact_is_skipped_not_inferred(self):
        self.account.contact_name = ""
        self.account.save()
        rows, skipped = build_vtb_debt_export(actor=self.actor)
        self.assertEqual(rows, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("ФИО", skipped[0].reason)

    def test_multiple_rows_use_real_crlf(self):
        second = Account.objects.create(
            number="0030142924", contact_name="Петров Петр", plot="г.Новый, дом 2",
        )
        Charge.objects.create(
            account=second, period=self.period, kind="service",
            amount=Decimal("10.00"), status="approved",
        )
        rows, skipped = build_vtb_debt_export(actor=self.actor)
        self.assertEqual(skipped, [])
        payload = render_vtb_debt_registry(rows)
        self.assertIn(b"\r\n", payload)
        self.assertNotIn(b"\\r\\n", payload)

    def test_structural_characters_are_skipped(self):
        cases = [
            ("number", "003;0142923"),
            ("contact_name", "Иванов\rИван"),
            ("plot", "г.Новый\nдом 1"),
        ]
        for field, bad_value in cases:
            with self.subTest(field=field):
                original = getattr(self.account, field)
                setattr(self.account, field, bad_value)
                self.account.save(update_fields=[field])
                rows, skipped = build_vtb_debt_export(actor=self.actor)
                self.assertEqual(rows, [])
                self.assertEqual(len(skipped), 1)
                self.assertIn("разделитель или перевод строки", skipped[0].reason)
                setattr(self.account, field, original)
                self.account.save(update_fields=[field])
