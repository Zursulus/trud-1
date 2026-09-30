from datetime import date

from django.test import TestCase

from .access_control import assignment_from_role
from .access_policy import ScopeType
from .models import Account, Membership, Person, SupplyNode, User, WaterGroup
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity


class MixedRoleModeSwitchTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username="switch-admin", is_staff=True)
        self.node = SupplyNode.objects.create(name="Switch node")
        self.line = WaterGroup.objects.create(name="Миндальная", node=self.node)
        self.account = Account.objects.create(number="SW-1", plot="Морская 34")
        Membership.objects.create(account=self.account, group=self.line, starts=date(2026, 1, 1))

    def make_user(self, slug, *, staff=False, resident=False):
        person = Person.objects.create(full_name=f"Switch {slug}")
        user = User.objects.create_user(username=slug, is_staff=staff)
        ResidentIdentity.objects.create(
            user=user, person=person, verified_by=self.admin, basis="Synthetic switch test",
        )
        if resident:
            PortalGrant.objects.create(
                person=person, account=self.account, starts=date(2026, 1, 1),
                can_view_account=True, can_submit_water=True,
                basis="Resident access", verified_by=self.admin,
            )
        if staff:
            assignment_from_role(
                person=person, role_code="line_senior",
                scope_type=ScopeType.WATER_GROUP, scope_object_id=self.line.pk,
                starts=date(2026, 1, 1), basis="Line senior", granted_by=self.admin,
            )
        return user

    def test_mixed_user_switches_both_ways_in_one_session(self):
        user = self.make_user("mixed-switch", staff=True, resident=True)
        self.client.force_login(user)

        resident = self.client.get(f"/admin/cabinet/account/{self.account.pk}/more/")
        self.assertEqual(resident.status_code, 200)
        self.assertContains(resident, "Рабочая база")
        self.assertContains(resident, 'href="/work/"')

        staff = self.client.get("/work/more/")
        self.assertEqual(staff.status_code, 200)
        self.assertContains(staff, "Кабинет жителя")
        self.assertContains(staff, 'href="/admin/cabinet/"')

    def test_resident_only_does_not_get_staff_switch(self):
        user = self.make_user("resident-switch", resident=True)
        self.client.force_login(user)
        resident = self.client.get(f"/admin/cabinet/account/{self.account.pk}/more/")
        self.assertNotContains(resident, "Переключиться в служебный режим")
        self.assertEqual(self.client.get("/work/more/").status_code, 302)

    def test_staff_only_does_not_get_resident_switch(self):
        user = self.make_user("staff-switch", staff=True)
        self.client.force_login(user)
        staff = self.client.get("/work/more/")
        self.assertEqual(staff.status_code, 200)
        self.assertNotContains(staff, "Кабинет жителя")
        self.assertNotContains(staff, 'href="/admin/cabinet/"')
