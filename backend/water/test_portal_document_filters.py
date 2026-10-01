"""Document filters and private appeal files use current canonical authority."""
from datetime import timedelta
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from public_site.models import PublicDocument, PublicDocumentCategory, PublicNews
from .models import Account, AccountDocument, AppealCategory, DocumentCategory, Person, ResidentAppeal, User
from .portal_permissions import PortalGrant
from .resident_models import ResidentAppealAttachment, ResidentAppealBoardMessage, ResidentAppealMessage, ResidentIdentity


class ResidentDocumentFilterTests(TestCase):
    payload = b"%PDF-1.4\nSynthetic document bytes\n%%EOF"

    def setUp(self):
        folder = TemporaryDirectory(prefix="trud-document-filters-")
        self.addCleanup(folder.cleanup)
        override = override_settings(MEDIA_ROOT=folder.name)
        override.enable()
        self.addCleanup(override.disable)
        self.today = timezone.localdate()
        self.staff = User.objects.create_user(username="documents-staff", is_staff=True)
        self.user = User.objects.create_user(username="documents-resident")
        self.neighbour = User.objects.create_user(username="documents-neighbour")
        self.account = Account.objects.create(number="DOCUMENTS-A", plot="Synthetic A")
        self.other = Account.objects.create(number="DOCUMENTS-B", plot="Synthetic B")
        self.grants = {}
        for user in (self.user, self.neighbour):
            person = Person.objects.create(full_name=f"Synthetic {user.username}")
            ResidentIdentity.objects.create(user=user, person=person, verified_by=self.staff, basis="Synthetic identity")
            for account in (self.account, self.other):
                self.grants[user.pk, account.pk] = PortalGrant.objects.create(
                    person=person, account=account, starts=self.today - timedelta(days=2),
                    can_view_documents=True, can_use_appeals=True, verified_by=self.staff, basis="Synthetic grant",
                )
        self.category = DocumentCategory.objects.create(name="Synthetic account category")
        self.appeal_category = AppealCategory.objects.create(name="Synthetic appeal category")
        self.public_category = PublicDocumentCategory.objects.create(name="Synthetic public category")
        self.personal = self._personal("Account document")
        self.public = PublicDocument.objects.create(
            category=self.public_category, title="Common document", document=self._file("common.pdf"),
            is_published=True, public_checked=True, document_date=self.today,
        )
        self.news = PublicNews.objects.create(
            title="Common news", summary="Synthetic summary", body="Synthetic body",
            is_published=True, public_checked=True, published_on=self.today,
        )
        self.appeal = self._appeal(self.user, self.account)
        self.file = self._attachment(self.appeal, "own-initial.pdf")
        self.url = reverse("resident_documents", args=[self.account.pk])
        self.client.force_login(self.user)

    def _file(self, name):
        return SimpleUploadedFile(name, self.payload, content_type="application/pdf")

    def _personal(self, title, **overrides):
        values = dict(account=self.account, category=self.category, title=title, document=self._file(title + ".pdf"))
        values.update(overrides)
        return AccountDocument.objects.create(**values)

    def _appeal(self, author, account):
        return ResidentAppeal.objects.create(
            account=account, author=author, category=self.appeal_category,
            subject="Synthetic question", message="Synthetic initial message",
        )

    def _attachment(self, appeal, name, **overrides):
        values = dict(appeal=appeal, uploaded_by=appeal.author, document=self._file(name))
        values.update(overrides)
        return ResidentAppealAttachment.objects.create(**values)

    def _download_url(self, attachment, account=None):
        return reverse("resident_appeal_attachment", args=[
            (account or self.account).pk, attachment.appeal_id, attachment.pk,
        ])

    def test_filters_have_independent_membership_and_native_active_links(self):
        titles = ["Account document", "Common document", "Common news", "own-initial.pdf"]
        for kind, visible in (
            ("all", titles), ("common", titles[1:3]), ("mine", [titles[0], titles[3]]), ("unknown", titles),
        ):
            with self.subTest(kind=kind):
                response = self.client.get(self.url, {"kind": kind})
                self.assertEqual(response.status_code, 200)
                expected_kind = "all" if kind == "unknown" else kind
                self.assertEqual(response.context["document_kind"], expected_kind)
                self.assertContains(response, 'aria-current="page"', count=1)
                for value in ("all", "common", "mine"):
                    self.assertContains(response, f'href="?kind={value}"')
                for title in titles:
                    if title in visible:
                        self.assertContains(response, title, count=1)
                    else:
                        self.assertNotContains(response, title)
                self.assertNotContains(response, "/media/")
                self.assertNotContains(response, "/private-data/")

    def test_hidden_future_and_unpublished_materials_stay_out_of_every_filter(self):
        self._personal("Hidden personal", visible_to_residents=False)
        self._personal("Future personal", published_at=timezone.now() + timedelta(days=1))
        for title, flags in (
            ("Draft public", {"is_published": False}),
            ("Unchecked public", {"public_checked": False}),
            ("Future public", {"document_date": self.today + timedelta(days=1)}),
        ):
            values = dict(is_published=True, public_checked=True, document_date=self.today)
            values.update(flags)
            PublicDocument.objects.create(category=self.public_category, title=title, document=self._file(title + ".pdf"), **values)
        PublicNews.objects.create(title="Unchecked news", is_published=True, public_checked=False)
        PublicNews.objects.create(title="Future news", is_published=True, public_checked=True, published_on=self.today + timedelta(days=1))
        for kind in ("all", "common", "mine"):
            response = self.client.get(self.url, {"kind": kind})
            for title in ("Hidden personal", "Future personal", "Draft public", "Unchecked public", "Future public", "Unchecked news", "Future news"):
                self.assertNotContains(response, title)

    def test_same_account_other_author_and_second_allowed_account_do_not_share_appeal_files(self):
        neighbour_file = self._attachment(self._appeal(self.neighbour, self.account), "neighbour-private.pdf")
        other_file = self._attachment(self._appeal(self.user, self.other), "other-account-private.pdf")
        self._personal("Other account document", account=self.other)
        for kind in ("all", "mine"):
            response = self.client.get(self.url, {"kind": kind})
            for title in ("neighbour-private.pdf", "other-account-private.pdf", "Other account document"):
                self.assertNotContains(response, title)
        self.assertEqual(self.client.get(self._download_url(neighbour_file)).status_code, 404)
        self.assertEqual(self.client.get(self._download_url(other_file)).status_code, 404)
        other_listing = self.client.get(reverse("resident_documents", args=[self.other.pk]), {"kind": "mine"})
        self.assertContains(other_listing, "other-account-private.pdf")
        self.assertNotContains(other_listing, "own-initial.pdf")
        self.client.force_login(self.neighbour)
        listing = self.client.get(self.url, {"kind": "mine"})
        self.assertContains(listing, "Account document")
        self.assertContains(listing, "neighbour-private.pdf")
        self.assertNotContains(listing, "own-initial.pdf")
        self.assertEqual(self.client.get(self._download_url(self.file)).status_code, 404)

    def test_initial_reply_and_board_files_in_closed_own_thread_remain_downloadable(self):
        reply = ResidentAppealMessage.objects.create(appeal=self.appeal, author=self.user, body="Synthetic reply")
        reply_file = self._attachment(self.appeal, "own-reply.pdf", message=reply)
        board = ResidentAppealBoardMessage.objects.create(appeal=self.appeal, author=self.staff, body="Synthetic board reply")
        board_file = self._attachment(self.appeal, "board-reply.pdf", uploaded_by=self.staff, board_message=board)
        self.appeal.status = "closed"
        self.appeal.response = "Synthetic final answer"
        self.appeal.responded_by = self.staff
        self.appeal.responded_at = timezone.now()
        self.appeal.save()
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        listing = self.client.get(self.url, {"kind": "mine"})
        for attachment in (self.file, reply_file, board_file):
            self.assertContains(listing, attachment.original_name, count=1)
            response = self.client.get(self._download_url(attachment))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), self.payload)
            self.assertTrue({"private", "no-store"}.issubset(set(response["Cache-Control"].split(", "))))
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(AccountDocument.objects.count(), 1)
        self.assertEqual(ResidentAppealAttachment.objects.count(), 3)

    def test_capability_revocation_in_existing_session_and_account_expiry(self):
        grant = self.grants[self.user.pk, self.account.pk]
        grant.can_use_appeals = False
        grant.save()
        listing = self.client.get(self.url, {"kind": "mine"})
        self.assertContains(listing, "Account document")
        self.assertNotContains(listing, "own-initial.pdf")
        self.assertEqual(self.client.get(self._download_url(self.file)).status_code, 404)
        grant.can_use_appeals = True
        grant.can_view_documents = False
        grant.save()
        for kind in ("all", "common", "mine"):
            self.assertEqual(self.client.get(self.url, {"kind": kind}).status_code, 404)
        response = self.client.get(self._download_url(self.file))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), self.payload)
        grant.can_view_documents = True
        grant.save()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        grant.ends = self.today
        grant.save()
        for kind in ("all", "common", "mine"):
            self.assertEqual(self.client.get(self.url, {"kind": kind}).status_code, 404)
        self.assertEqual(self.client.get(self._download_url(self.file)).status_code, 404)
        self.assertTrue(ResidentAppealAttachment.objects.filter(pk=self.file.pk).exists())
        self.assertTrue(self.file.document.storage.exists(self.file.document.name))

    def test_empty_mine_and_attachment_only_mine_have_correct_states(self):
        self.client.force_login(self.neighbour)
        listing = self.client.get(self.url, {"kind": "mine"})
        self.assertContains(listing, "Account document")
        self.personal.visible_to_residents = False
        self.personal.save()
        listing = self.client.get(self.url, {"kind": "mine"})
        self.assertContains(listing, "Личных документов и вложений пока нет.")
        self.assertNotContains(listing, "Common document")
        self.client.force_login(self.user)
        listing = self.client.get(self.url, {"kind": "mine"})
        self.assertContains(listing, "own-initial.pdf")
        self.assertNotContains(listing, "Личных документов и вложений пока нет.")
        self.public.is_published = False
        self.public.save()
        self.news.is_published = False
        self.news.save()
        self.assertContains(self.client.get(self.url, {"kind": "common"}), "Общих документов и новостей пока нет.")
