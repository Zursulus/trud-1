from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .access_control import AccessAssignment
from .models import Account, Meter, Person, SupplyNode, User
from .resident_models import ResidentIdentity


class WorkbenchMeterBindingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.admin = User.objects.create_user(username="meter-admin", is_staff=True, is_superuser=True)
        cls.denied = User.objects.create_user(username="meter-denied", is_staff=True)
        cls.account = Account.objects.create(number="METER-131", plot="Тестовый участок 131")
        cls.node = SupplyNode.objects.create(name="Тестовый узел 131")
        cls.other_node = SupplyNode.objects.create(name="Чужой узел 132")
        cls.binder = User.objects.create_user(username="scoped-meter-binder", is_staff=True)
        person = Person.objects.create(full_name="Редактор одного узла")
        ResidentIdentity.objects.create(
            user=cls.binder, person=person, verified_by=cls.admin, basis="Синтетическая проверка привязки",
        )
        for scope_type, object_id, capabilities in (
            ("account", cls.account.pk, ["accounts.view", "water.view", "water.meters.view"]),
            ("supply_node", cls.node.pk, ["water.topology.manage"]),
        ):
            AccessAssignment.objects.create(
                person=person, role_code=f"test-meter-{scope_type}", role_version=1,
                role_label="Ограниченная привязка", allowed_capabilities=capabilities,
                capabilities=capabilities, scope_type=scope_type, scope_object_id=object_id,
                starts=timezone.localdate(), basis="Синтетическая проверка привязки", granted_by=cls.admin,
            )

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

    def test_scoped_binder_can_discover_binding_from_daily_account_card(self):
        self.client.force_login(self.binder)
        self.assertEqual(self.client.get("/work/panel/").status_code, 403)
        response = self.client.get(f"/work/accounts/{self.account.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Привязать счётчик")
        self.assertContains(response, f'/work/accounts/{self.account.pk}/meters/bind/')

    def test_scoped_binder_cancel_returns_to_accessible_account(self):
        self.client.force_login(self.binder)
        url = f"/work/accounts/{self.account.pk}/meters/bind/"
        response = self.client.get(url)
        self.assertContains(response, f'href="/work/accounts/{self.account.pk}/"')

    def test_scoped_binder_success_returns_to_accessible_account(self):
        self.client.force_login(self.binder)
        url = f"/work/accounts/{self.account.pk}/meters/bind/"
        response = self.client.post(url, {
            "node": self.node.pk, "serial": "SCOPED-BIND-131", "commissioned_on": "",
            "seal_number": "", "notes": "Синтетический счётчик",
        })
        self.assertRedirects(response, f"/work/accounts/{self.account.pk}/")
        meter = Meter.objects.get(serial="SCOPED-BIND-131")
        self.assertEqual(meter.account_id, self.account.pk)
        self.assertEqual(meter.node_id, self.node.pk)
        self.assertEqual(meter.history.first().history_user_id, self.binder.pk)

    def test_binding_from_account_keeps_search_context_even_for_workbench_user(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/work/accounts/{self.account.pk}/meters/bind/?from=account&q=METER-131",
            {"node": self.node.pk, "serial": "ACCOUNT-BIND-131", "commissioned_on": "",
             "seal_number": "", "notes": ""},
        )
        self.assertRedirects(response, f"/work/accounts/{self.account.pk}/?q=METER-131")

    def test_workbench_binding_keeps_existing_accessible_return(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/work/accounts/{self.account.pk}/meters/bind/",
            {"node": self.node.pk, "serial": "PANEL-BIND-131", "commissioned_on": "",
             "seal_number": "", "notes": ""},
        )
        self.assertRedirects(response, f"/work/panel/?kind=account&id={self.account.pk}")

    def test_scoped_binder_cannot_use_another_node_or_account(self):
        self.client.force_login(self.binder)
        before = Meter.objects.count()
        response = self.client.post(
            f"/work/accounts/{self.account.pk}/meters/bind/?from=account",
            {"node": self.other_node.pk, "serial": "FORGED-BIND", "commissioned_on": "",
             "seal_number": "", "notes": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors.get("node"))
        other_account = Account.objects.create(number="METER-OTHER", plot="Чужой участок")
        response = self.client.post(
            f"/work/accounts/{other_account.pk}/meters/bind/",
            {"node": self.node.pk, "serial": "FORGED-ACCOUNT", "commissioned_on": "",
             "seal_number": "", "notes": ""},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Meter.objects.count(), before)
