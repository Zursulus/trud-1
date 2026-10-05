from io import StringIO
from urllib.parse import urlencode

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .access_control import AccessAssignment
from .models import Account, Person, User
from .resident_models import ResidentIdentity


class AccountEditorPrivacyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.administrator = User.objects.create_user(username="plain-account-admin", is_staff=True)
        cls.administrator.groups.add(Group.objects.get(name="Администратор ТСН"))
        cls.private_editor = User.objects.create_user(username="private-account-admin", is_staff=True)
        cls.private_editor.groups.add(
            Group.objects.get(name="Администратор ТСН"),
            Group.objects.get(name="Закрытый реестр членов ТСН"),
        )
        cls.private_reader = User.objects.create_user(username="private-account-reader", is_staff=True)
        cls.private_reader.groups.add(Group.objects.get(name="Закрытый реестр членов ТСН"))
        cls.account = Account.objects.create(
            number="PRIVACY-125", plot="Тестовый участок 125",
            contact_name="Закрытый legacy-контакт", phone="+70000000125", notes="Исходная заметка",
        )

    def _url(self, account=None):
        return f"/work/accounts/{(account or self.account).pk}/edit/"

    def _post_data(self, **extra):
        return {
            "plot": "Новый ориентир 125", "notes": "Проверенная рабочая заметка",
            "version": self.account.version, **extra,
        }

    def _assert_private_values_absent(self, response):
        self.assertNotContains(response, self.account.contact_name)
        self.assertNotContains(response, self.account.phone)
        self.assertNotIn("contact_name", response.context["form"].fields)
        self.assertNotIn("phone", response.context["form"].fields)

    def _assert_contact_values_preserved(self):
        self.account.refresh_from_db()
        self.assertEqual(self.account.contact_name, "Закрытый legacy-контакт")
        self.assertEqual(self.account.phone, "+70000000125")
        latest = self.account.history.latest()
        self.assertEqual(latest.contact_name, self.account.contact_name)
        self.assertEqual(latest.phone, self.account.phone)

    def test_ordinary_administrator_form_hides_legacy_contacts(self):
        self.client.force_login(self.administrator)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self._assert_private_values_absent(response)
        self.assertEqual(set(response.context["form"].fields), {"plot", "notes", "version"})

    def test_ordinary_administrator_forged_contact_post_only_saves_work_fields(self):
        self.client.force_login(self.administrator)
        old_version = self.account.version
        old_history = self.account.history.count()
        response = self.client.post(self._url(), self._post_data(
            contact_name="Подставленный контакт", phone="+79999999999",
        ))
        self.assertRedirects(response, f"/work/accounts/{self.account.pk}/")
        self._assert_contact_values_preserved()
        self.assertEqual(self.account.plot, "Новый ориентир 125")
        self.assertEqual(self.account.notes, "Проверенная рабочая заметка")
        self.assertEqual(self.account.version, old_version + 1)
        self.assertEqual(self.account.history.count(), old_history + 1)
        self.assertEqual(self.account.history.latest().history_user_id, self.administrator.pk)

    def test_ordinary_administrator_post_without_contact_fields_preserves_them(self):
        self.client.force_login(self.administrator)
        response = self.client.post(self._url(), self._post_data())
        self.assertRedirects(response, f"/work/accounts/{self.account.pk}/")
        self._assert_contact_values_preserved()

    def test_validation_error_does_not_echo_forged_contact_fields_or_write(self):
        self.client.force_login(self.administrator)
        old_history = self.account.history.count()
        response = self.client.post(self._url(), self._post_data(
            version=self.account.version + 1,
            contact_name="Подставленный контакт", phone="+79999999999",
        ))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Запись уже изменена другим пользователем")
        self._assert_private_values_absent(response)
        self.assertNotContains(response, "Подставленный контакт")
        self.assertNotContains(response, "+79999999999")
        self._assert_contact_values_preserved()
        self.assertEqual(self.account.history.count(), old_history)
        self.assertEqual(self.account.notes, "Исходная заметка")

    def test_non_superuser_with_global_private_access_keeps_contact_edit_and_history(self):
        self.assertFalse(self.private_editor.is_superuser)
        self.client.force_login(self.private_editor)
        response = self.client.get(self._url())
        self.assertContains(response, self.account.contact_name)
        self.assertContains(response, self.account.phone)
        old_version = self.account.version
        response = self.client.post(self._url(), self._post_data(
            contact_name="Проверенный закрытый контакт", phone="+70000000999",
        ))
        self.assertRedirects(response, f"/work/accounts/{self.account.pk}/")
        self.account.refresh_from_db()
        self.assertEqual(self.account.contact_name, "Проверенный закрытый контакт")
        self.assertEqual(self.account.phone, "+70000000999")
        self.assertEqual(self.account.version, old_version + 1)
        self.assertEqual(self.account.history.latest().history_user_id, self.private_editor.pk)

    def test_private_registry_alone_does_not_grant_account_edit(self):
        self.client.force_login(self.private_reader)
        before = self.account.history.count()
        self.assertEqual(self.client.get(self._url()).status_code, 403)
        self.assertEqual(self.client.post(self._url(), self._post_data()).status_code, 403)
        self._assert_contact_values_preserved()
        self.assertEqual(self.account.history.count(), before)

    def test_person_scoped_contacts_do_not_authorize_unmapped_account_legacy_contacts(self):
        scoped_person = Person.objects.create(full_name="Ограниченный закрытый реестр")
        scoped_editor = User.objects.create_user(username="person-scoped-private-editor", is_staff=True)
        scoped_editor.groups.add(Group.objects.get(name="Администратор ТСН"))
        ResidentIdentity.objects.create(
            user=scoped_editor, person=scoped_person, verified_by=self.private_editor,
            basis="Синтетическая связь для проверки области",
        )
        AccessAssignment.objects.create(
            person=scoped_person, role_code="test-person-contacts", role_version=1,
            role_label="Контакты одного человека", allowed_capabilities=["registry.contacts.view"],
            capabilities=["registry.contacts.view"], scope_type="person", scope_object_id=scoped_person.pk,
            starts=timezone.localdate(), basis="Синтетическая проверка области", granted_by=self.private_editor,
        )
        self.client.force_login(scoped_editor)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self._assert_private_values_absent(response)
        response = self.client.post(self._url(), self._post_data(
            contact_name="Чужой legacy-контакт", phone="+79999999998",
        ))
        self.assertRedirects(response, f"/work/accounts/{self.account.pk}/")
        self._assert_contact_values_preserved()

    def test_account_edit_return_keeps_only_bounded_search_query(self):
        self.client.force_login(self.administrator)
        raw_query = "  номер   &next=https://example.test/ " + "1" * 200
        expected_query = " ".join(raw_query.split())[:160]
        response = self.client.post(
            self._url() + "?" + urlencode({"q": raw_query, "next": "https://example.test/"}),
            self._post_data(),
        )
        self.assertRedirects(response, f"/work/accounts/{self.account.pk}/?" + urlencode({"q": expected_query}))
        self._assert_contact_values_preserved()
