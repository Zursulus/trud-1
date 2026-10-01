from datetime import date

from django.contrib.auth import SESSION_KEY
from django.test import Client, TestCase
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

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

    def test_staff_only_logout_clears_session_and_can_login_as_resident(self):
        staff = self.make_user("logout-staff", staff=True)
        resident = self.make_user("logout-resident", resident=True)
        resident.set_password("synthetic-logout-password")
        resident.save()
        client = Client(enforce_csrf_checks=True)
        client.force_login(staff)
        device = TOTPDevice.objects.create(user=staff, name="synthetic-logout-device", confirmed=True)
        session = client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session["logout-test-marker"] = "staff-session"
        session.save()

        page = client.get("/work/more/")
        self.assertContains(page, 'action="/admin/cabinet/logout/"')
        self.assertContains(page, 'type="submit" class="ws-secondary">Выйти</button>')
        self.assertNotContains(page, "Кабинет жителя")
        self.assertEqual(client.session[DEVICE_ID_SESSION_KEY], device.persistent_id)
        grants_before = list(PortalGrant.objects.values())
        logout = client.post(reverse("resident_logout"), {
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        })
        self.assertRedirects(logout, reverse("resident_login"))
        for key in (SESSION_KEY, DEVICE_ID_SESSION_KEY, "logout-test-marker"):
            self.assertNotIn(key, client.session)
        self.assertEqual(list(PortalGrant.objects.values()), grants_before)
        self.assertEqual(client.get("/work/").status_code, 302)

        login = client.post(reverse("resident_login"), {
            "username": resident.username, "password": "synthetic-logout-password",
            "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
        })
        self.assertRedirects(login, reverse("resident_dashboard"))
        self.assertEqual(client.session[SESSION_KEY], str(resident.pk))
        self.assertEqual(client.get("/work/").status_code, 302)

    def test_logout_requires_post_and_csrf_without_ending_session(self):
        staff = self.make_user("logout-protected", staff=True)
        client = Client(enforce_csrf_checks=True)
        client.force_login(staff)
        self.assertEqual(client.get(reverse("resident_logout")).status_code, 405)
        self.assertEqual(client.post(reverse("resident_logout")).status_code, 403)
        self.assertEqual(client.session[SESSION_KEY], str(staff.pk))
        self.assertEqual(client.get("/work/").status_code, 200)
