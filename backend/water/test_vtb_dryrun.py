from io import StringIO
import tempfile

from django.core.management import call_command
from django.test import TestCase

from .models import Account, Payment


class VtbDryRunCommandTests(TestCase):
    def test_dry_run_matches_exact_account_and_never_creates_payment(self):
        Account.objects.create(number="ACC-001", plot="Тестовый участок")
        payload = (
            "27-01-2020;16-07-15;4;427285406;abc123;ACC-001;Тест Тестов;"
            "г.Тест;0120;715.20;715.20;0.00\r\n"
            "=1;715.20;715.20;0.00;8;28-01-2020"
        ).encode("cp1251")
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(payload)
            handle.flush()
            stdout = StringIO()
            call_command("vtb_registry_dryrun", handle.name, stdout=stdout)
        output = stdout.getvalue()
        self.assertIn("matched=1", output)
        self.assertIn("NO DATABASE WRITES PERFORMED", output)
        self.assertEqual(Payment.objects.count(), 0)

    def test_dry_run_reports_unknown_account_without_guessing(self):
        payload = (
            "27-01-2020;16-07-15;4;427285406;abc123;UNKNOWN;Иванов Иван;"
            "г.Тест;0120;10.00;10.00;0.00\r\n"
            "=1;10.00;10.00;0.00;8;28-01-2020"
        ).encode("utf-8")
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(payload)
            handle.flush()
            stdout = StringIO()
            call_command("vtb_registry_dryrun", handle.name, stdout=stdout)
        self.assertIn("unmatched=1", stdout.getvalue())
        self.assertEqual(Payment.objects.count(), 0)
