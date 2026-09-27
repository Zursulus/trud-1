from datetime import timedelta
from io import StringIO
from tempfile import TemporaryDirectory

from django.contrib.admin.models import ADDITION, LogEntry
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from public_site.models import PublicDocument, PublicDocumentCategory, PublicNews

from .models import Account, AccountDocument, DocumentCategory, ResidentAccess, User


ADMIN = "Администратор ТСН"
OPERATOR = "Оператор воды"


class StaffWorkspaceDocumentsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.admin_user = User.objects.create_user(username="documents-admin", is_staff=True)
        cls.admin_user.groups.add(Group.objects.get(name=ADMIN))
        cls.operator = User.objects.create_user(username="documents-operator", is_staff=True)
        cls.operator.groups.add(Group.objects.get(name=OPERATOR))
        cls.viewer = User.objects.create_user(username="documents-viewer", is_staff=True)
        cls.viewer.user_permissions.add(Permission.objects.get(codename="view_accountdocument", content_type__app_label="water"))

        cls.account = Account.objects.create(
            number="DOC-001",
            plot="Документный участок",
            contact_name="Скрытое Контактное Лицо",
            phone="+7 900 999-88-77",
        )
        cls.other_account = Account.objects.create(number="DOC-002", plot="Другой участок")
        cls.category = DocumentCategory.objects.create(name="WS84 Квитанция", sort_order=10)
        cls.public_category = PublicDocumentCategory.objects.create(name="WS84 Протоколы", sort_order=10)

    def setUp(self):
        self._media = TemporaryDirectory()
        self._media_override = override_settings(MEDIA_ROOT=self._media.name)
        self._media_override.enable()

    def tearDown(self):
        self._media_override.disable()
        self._media.cleanup()

    def login(self, user):
        self.client.force_login(user)

    def make_account_document(self, *, title="Личный документ", visible=True, published_at=None):
        return AccountDocument.objects.create(
            account=self.account,
            category=self.category,
            title=title,
            document=SimpleUploadedFile("resident.pdf", b"%PDF-1.4 test"),
            published_at=published_at or timezone.now(),
            visible_to_residents=visible,
        )

    def make_public_document(self, *, title="Публичный документ"):
        return PublicDocument.objects.create(
            category=self.public_category,
            title=title,
            document=SimpleUploadedFile("public.pdf", b"%PDF-1.4 public"),
            document_date=timezone.localdate(),
        )

    def test_admin_sees_three_lanes_without_account_pii(self):
        item = self.make_account_document()
        self.login(self.admin_user)
        response = self.client.get("/work/documents/", {"q": "DOC-001"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Документы жителей")
        self.assertContains(response, "Публичные документы")
        self.assertContains(response, "Новости сайта")
        self.assertContains(response, item.title)
        self.assertContains(response, self.account.number)
        self.assertContains(response, self.account.plot)
        self.assertNotContains(response, self.account.contact_name)
        self.assertNotContains(response, self.account.phone)

    def test_operator_cannot_open_documents_workspace(self):
        self.login(self.operator)
        self.assertEqual(self.client.get("/work/documents/").status_code, 403)
        self.assertEqual(self.client.get("/work/documents/accounts/new/").status_code, 403)

    def test_view_only_permission_cannot_mutate_by_direct_post(self):
        item = self.make_account_document()
        original_title = item.title
        self.login(self.viewer)
        self.assertEqual(self.client.get("/work/documents/").status_code, 200)
        self.assertEqual(self.client.get(f"/work/documents/accounts/{item.pk}/").status_code, 200)
        response = self.client.post(
            f"/work/documents/accounts/{item.pk}/",
            {
                "category": self.category.pk,
                "title": "Попытка изменения",
                "published_at": timezone.localtime(item.published_at).strftime("%Y-%m-%dT%H:%M"),
                "visible_to_residents": "on",
                "change_reason": "Не должно сработать",
            },
        )
        self.assertEqual(response.status_code, 403)
        item.refresh_from_db()
        self.assertEqual(item.title, original_title)

    def test_account_document_metadata_edit_keeps_file_and_account_and_requires_reason(self):
        item = self.make_account_document()
        original_file = item.document.name
        original_account = item.account_id
        original_title = item.title
        self.login(self.admin_user)
        url = f"/work/documents/accounts/{item.pk}/"
        without_reason = self.client.post(
            url,
            {
                "category": self.category.pk,
                "title": "Новое название",
                "published_at": timezone.localtime(item.published_at).strftime("%Y-%m-%dT%H:%M"),
                "visible_to_residents": "on",
                "notes": "Проверка",
                "change_reason": "",
            },
        )
        self.assertEqual(without_reason.status_code, 200)
        self.assertIn("change_reason", without_reason.context["form"].errors)
        item.refresh_from_db()
        self.assertEqual(item.title, original_title)

        changed = self.client.post(
            url,
            {
                "category": self.category.pk,
                "title": "Новое название",
                "published_at": timezone.localtime(item.published_at).strftime("%Y-%m-%dT%H:%M"),
                "visible_to_residents": "on",
                "notes": "Проверка",
                "change_reason": "Уточнено название",
                "account": self.other_account.pk,
                "document": SimpleUploadedFile("replacement.pdf", b"replacement"),
            },
        )
        self.assertEqual(changed.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.title, "Новое название")
        self.assertEqual(item.document.name, original_file)
        self.assertEqual(item.account_id, original_account)

    def test_public_news_publish_requires_confirmation_and_records_actor(self):
        self.login(self.admin_user)
        url = "/work/documents/news/new/"
        payload = {
            "title": "Проверяемая новость",
            "category": "НОВОСТЬ",
            "summary": "Кратко",
            "body": "Текст новости",
            "published_on": timezone.localdate().isoformat(),
            "is_published": "on",
        }
        rejected = self.client.post(url, payload)
        self.assertEqual(rejected.status_code, 200)
        self.assertContains(rejected, "Перед публикацией проверьте материал")
        self.assertFalse(PublicNews.objects.filter(title="Проверяемая новость").exists())

        published = self.client.post(url, {**payload, "confirm_publication": "on"})
        self.assertEqual(published.status_code, 302)
        item = PublicNews.objects.get(title="Проверяемая новость")
        self.assertTrue(item.is_published)
        self.assertTrue(item.public_checked)
        self.assertEqual(item.published_by, self.admin_user)
        self.assertIsNotNone(item.published_at)
        log = LogEntry.objects.get(object_id=str(item.pk), content_type__app_label="public_site", content_type__model="publicnews")
        self.assertEqual(log.action_flag, ADDITION)

    def test_unpublish_clears_publication_audit_and_requires_change_reason(self):
        item = PublicNews.objects.create(
            title="Опубликованная новость",
            category="НОВОСТЬ",
            summary="Анонс",
            body="Текст",
            published_on=timezone.localdate(),
            is_published=True,
            public_checked=True,
            published_at=timezone.now(),
            published_by=self.admin_user,
        )
        self.login(self.admin_user)
        url = f"/work/documents/news/{item.pk}/"
        payload = {
            "title": item.title,
            "category": item.category,
            "summary": item.summary,
            "body": item.body,
            "published_on": item.published_on.isoformat(),
            "change_reason": "Снять с публикации",
        }
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertFalse(item.is_published)
        self.assertFalse(item.public_checked)
        self.assertIsNone(item.published_at)
        self.assertIsNone(item.published_by)

    def test_public_document_edit_cannot_replace_file(self):
        item = self.make_public_document()
        original_file = item.document.name
        self.login(self.admin_user)
        response = self.client.post(
            f"/work/documents/public/{item.pk}/",
            {
                "category": self.public_category.pk,
                "title": "Обновлённый заголовок",
                "description": "Описание",
                "document_date": item.document_date.isoformat(),
                "notes": "Служебно",
                "change_reason": "Исправлен заголовок",
                "document": SimpleUploadedFile("replacement.pdf", b"replacement"),
            },
        )
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.title, "Обновлённый заголовок")
        self.assertEqual(item.document.name, original_file)

    def test_resident_portal_still_filters_hidden_and_future_account_documents(self):
        resident = User.objects.create_user(username="documents-resident", is_staff=False)
        ResidentAccess.objects.create(
            user=resident,
            account=self.account,
            role="owner",
            starts=timezone.localdate() - timedelta(days=1),
        )
        visible = self.make_account_document(title="Виден жителю")
        hidden = self.make_account_document(title="Скрыт от жителя", visible=False)
        future = self.make_account_document(
            title="Будущий документ",
            visible=True,
            published_at=timezone.now() + timedelta(days=2),
        )
        self.client.force_login(resident)
        response = self.client.get(f"/admin/cabinet/account/{self.account.pk}/documents/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, visible.title)
        self.assertNotContains(response, hidden.title)
        self.assertNotContains(response, future.title)
        self.assertEqual(
            self.client.get(f"/admin/cabinet/account/{self.account.pk}/document/{hidden.pk}/").status_code,
            404,
        )

    def test_documents_dashboard_query_count_is_bounded(self):
        self.make_account_document()
        self.make_public_document()
        PublicNews.objects.create(
            title="Черновик",
            category="НОВОСТЬ",
            summary="Анонс",
            body="Текст",
            published_on=timezone.localdate(),
        )
        self.login(self.admin_user)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/work/documents/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 35, len(queries))
