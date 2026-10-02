from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from io import StringIO

from .models import Account, Payment, User


class VtbDryRunViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.staff = User.objects.create_user(username="vtb-viewer", is_staff=True, is_superuser=True)
        cls.denied = User.objects.create_user(username="vtb-denied", is_staff=True)
        cls.account = Account.objects.create(number="ACC-001", plot="Тест ВТБ")

    def _file(self, account="ACC-001"):
        text = (
            f"27-01-2020;16-07-15;4;427285406;abc123;{account};Тест Тестов;"
            "г.Тест;0120;715.20;715.20;0.00\r\n"
            "=1;715.20;715.20;0.00;8;28-01-2020"
        )
        return SimpleUploadedFile("payments.txt", text.encode("cp1251"), content_type="text/plain")

    def test_exact_account_match_is_preview_only(self):
        self.client.force_login(self.staff)
        response = self.client.post("/work/finance/vtb/payments/dry-run/", {"encoding": "cp1251", "registry": self._file()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Совпало:</strong> 1")
        self.assertContains(response, "Никаких записей не выполнено")
        self.assertEqual(Payment.objects.count(), 0)

    def test_unknown_account_is_not_guessed_from_name_or_address(self):
        self.client.force_login(self.staff)
        response = self.client.post("/work/finance/vtb/payments/dry-run/", {"encoding": "cp1251", "registry": self._file("UNKNOWN")})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Не найдено:</strong> 1")
        self.assertEqual(Payment.objects.count(), 0)

    def test_staff_without_finance_view_is_denied(self):
        self.client.force_login(self.denied)
        self.assertEqual(self.client.get("/work/finance/vtb/payments/dry-run/").status_code, 403)
