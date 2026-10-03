from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from .models import Account, Meter, SupplyNode, User


class WorkbenchMeterBindingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.admin = User.objects.create_user(username="meter-admin", is_staff=True, is_superuser=True)
        cls.denied = User.objects.create_user(username="meter-denied", is_staff=True)
        cls.account = Account.objects.create(number="METER-131", plot="Тестовый участок 131")
        cls.node = SupplyNode.objects.create(name="Тестовый узел 131")

    def test_create_and_bind_individual_meter_is_audited(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/work/accounts/{self.account.pk}/meters/bind/",
            {"node": self.node.pk, "serial": "TEST-METER-131", "commissioned_on": "", "seal_number": "S131", "notes": "synthetic"},
        )
        self.assertEqual(response.status_code, 302)
        meter = Meter.objects.get(serial="TEST-METER-131")
        self.assertEqual(meter.kind, "individual")
        self.assertEqual(meter.account_id, self.account.pk)
        self.assertEqual(meter.node_id, self.node.pk)
        self.assertEqual(meter.history.first().history_change_reason, "Индивидуальный счётчик привязан в Staff Workspace")

    def test_duplicate_node_serial_fails_without_extra_meter(self):
        Meter.objects.create(serial="DUP-131", kind="individual", node=self.node, account=self.account)
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/work/accounts/{self.account.pk}/meters/bind/",
            {"node": self.node.pk, "serial": "DUP-131", "commissioned_on": "", "seal_number": "", "notes": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Счётчик с таким номером уже существует")
        self.assertEqual(Meter.objects.filter(serial="DUP-131", node=self.node).count(), 1)

    def test_staff_without_capability_is_denied(self):
        self.client.force_login(self.denied)
        self.assertEqual(self.client.get(f"/work/accounts/{self.account.pk}/meters/bind/").status_code, 403)
