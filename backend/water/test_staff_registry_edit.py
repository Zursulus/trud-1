from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .access_control import AccessAssignment
from .models import Account, LandPlot, Person, User
from .resident_models import ResidentIdentity


class StaffRegistryEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.editor = User.objects.create_user(username="registry-editor", is_staff=True, is_superuser=True)
        cls.editor.groups.add(
            Group.objects.get(name="Администратор ТСН"),
            Group.objects.get(name="Закрытый реестр членов ТСН"),
        )
        cls.denied = User.objects.create_user(username="registry-denied", is_staff=True)
        cls.account = Account.objects.create(
            number="EDIT-125", plot="Старый адрес", contact_name="Старый контакт", phone="+70000000000",
        )
        cls.plot = LandPlot.objects.create(
            label="Участок 125", address="Старый ориентир", account=cls.account,
        )
        cls.person = Person.objects.create(
            full_name="Редактируемый Житель", phone="+71111111111", email="old@example.test",
        )

    def test_person_contact_edit_preserves_history_and_increments_version(self):
        self.client.force_login(self.editor)
        before = self.person.version
        response = self.client.post(
            f"/work/access/people/{self.person.pk}/edit/",
            {
                "full_name": "Редактируемый Житель",
                "phone": "+79991234567",
                "email": "new@example.test",
                "notes": "Проверенный контакт",
                "version": before,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.person.refresh_from_db()
        self.assertEqual(self.person.phone, "+79991234567")
        self.assertEqual(self.person.email, "new@example.test")
        self.assertEqual(self.person.version, before + 1)
        self.assertGreaterEqual(self.person.history.count(), 2)

    def test_account_contact_and_land_plot_address_are_separate_edits(self):
        self.client.force_login(self.editor)
        response = self.client.post(
            f"/work/accounts/{self.account.pk}/edit/",
            {
                "plot": "Горная 2",
                "contact_name": "Новый контакт",
                "phone": "+79990000001",
                "notes": "",
                "version": self.account.version,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.account.refresh_from_db()
        self.plot.refresh_from_db()
        self.assertEqual(self.account.plot, "Горная 2")
        self.assertEqual(self.account.phone, "+79990000001")
        self.assertEqual(self.plot.address, "Старый ориентир")

        response = self.client.post(
            f"/work/plots/{self.plot.pk}/edit/",
            {
                "label": self.plot.label,
                "address": "Горная 2, новый ориентир",
                "cadastral_number": "",
                "area_m2": "",
                "notes": "",
                "version": self.plot.version,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.plot.refresh_from_db()
        self.assertEqual(self.plot.address, "Горная 2, новый ориентир")
        self.assertGreaterEqual(self.plot.history.count(), 2)

    def test_stale_person_form_does_not_overwrite_newer_change(self):
        self.client.force_login(self.editor)
        stale_version = self.person.version
        self.person.notes = "Более свежая правка"
        self.person.save()
        current_version = self.person.version

        response = self.client.post(
            f"/work/access/people/{self.person.pk}/edit/",
            {
                "full_name": self.person.full_name,
                "phone": "+78888888888",
                "email": self.person.email,
                "notes": "Устаревшая форма",
                "version": stale_version,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Запись уже изменена другим пользователем")
        self.person.refresh_from_db()
        self.assertEqual(self.person.notes, "Более свежая правка")
        self.assertEqual(self.person.version, current_version)
        self.assertNotEqual(self.person.phone, "+78888888888")

    def test_staff_without_edit_capabilities_is_denied(self):
        self.client.force_login(self.denied)
        self.assertEqual(self.client.get(f"/work/access/people/{self.person.pk}/edit/").status_code, 403)
        self.assertEqual(self.client.get(f"/work/accounts/{self.account.pk}/edit/").status_code, 403)
        self.assertEqual(self.client.get(f"/work/plots/{self.plot.pk}/edit/").status_code, 403)


    def test_account_scoped_editor_cannot_open_another_account(self):
        scoped_user = User.objects.create_user(username="scoped-account-editor", is_staff=True)
        scoped_person = Person.objects.create(full_name="Ограниченный редактор")
        ResidentIdentity.objects.create(
            user=scoped_user, person=scoped_person, verified_by=self.editor, basis="Тест scope",
        )
        AccessAssignment.objects.create(
            person=scoped_person,
            role_code="test-account-editor",
            role_version=1,
            role_label="Редактор одного счёта",
            allowed_capabilities=["accounts.view", "accounts.edit"],
            capabilities=["accounts.view", "accounts.edit"],
            scope_type="account",
            scope_object_id=self.account.pk,
            starts=timezone.localdate(),
            basis="Тест ограниченной области",
            granted_by=self.editor,
        )
        other = Account.objects.create(number="EDIT-OTHER", plot="Чужой участок")

        self.client.force_login(scoped_user)
        allowed = self.client.get(f"/work/accounts/{self.account.pk}/edit/")
        self.assertEqual(allowed.status_code, 200)
        denied = self.client.get(f"/work/accounts/{other.pk}/edit/")
        self.assertEqual(denied.status_code, 404)

    def test_validation_error_keeps_original_person_data(self):
        self.client.force_login(self.editor)
        before = self.person.version
        response = self.client.post(
            f"/work/access/people/{self.person.pk}/edit/",
            {
                "full_name": "   ",
                "phone": "+77777777777",
                "email": self.person.email,
                "notes": "",
                "version": before,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.person.refresh_from_db()
        self.assertEqual(self.person.full_name, "Редактируемый Житель")
        self.assertEqual(self.person.version, before)
