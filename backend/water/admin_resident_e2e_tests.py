"""Two simultaneous roles using real login, CSRF and canonical grants (#121)."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

from axe_playwright_python.sync_playwright import Axe
from django.conf import settings
from django.contrib.auth.models import Group
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.management import call_command
from django.db import connections
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from .models import Account, AppealCategory, Person, ResidentAccess, ResidentAppeal, User
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity


class AdminResidentBrowserTests(StaticLiveServerTestCase):
    password = "synthetic-browser-password"

    def setUp(self):
        call_command("setup_roles", stdout=StringIO())
        self.staff = User.objects.create_user(username="contract-browser-staff", password=self.password, is_staff=True)
        self.staff.groups.add(Group.objects.get(name="Администратор ТСН"))
        self.resident = User.objects.create_user(username="contract-browser-resident", password=self.password)
        self.account = Account.objects.create(number="BROWSER-A", plot="Synthetic browser plot")
        self.category = AppealCategory.objects.create(name="Synthetic browser category")
        for user in (self.staff, self.resident):
            person = Person.objects.create(full_name=f"Synthetic {user.username}")
            ResidentIdentity.objects.create(user=user, person=person, verified_by=self.staff, basis="Synthetic identity")
            PortalGrant.objects.create(
                person=person, account=self.account, starts=timezone.localdate() - timedelta(days=1),
                can_view_account=True, can_use_appeals=True, basis="Synthetic browser grant", verified_by=self.staff,
            )
        self.assertFalse(ResidentAccess.objects.filter(user__in=[self.staff, self.resident]).exists())

    def _login(self, page, user):
        path = "/admin/login/?next=/work/appeals/" if user.is_staff else "/admin/cabinet/login/"
        page.goto(self.live_server_url + path)
        page.locator("#id_username").fill(user.username)
        page.locator("#id_password").fill(self.password)
        if user.is_staff:
            page.locator('#login-form input[type="submit"]').click()
            page.wait_for_url("**/work/appeals/")
        else:
            page.get_by_role("button", name="Войти", exact=True).click()
            page.wait_for_url("**/admin/cabinet/")
        page.wait_for_load_state("networkidle")

    def _assert_accessibility(self, page):
        blocking = [v for v in Axe().run(page).response.get("violations", [])
                    if v.get("impact") in {"serious", "critical"}
                    and any(str(t).startswith("wcag") for t in v.get("tags") or [])]
        self.assertEqual(blocking, [])
        self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"))

    def _db(self, operation):
        # Playwright's sync API runs an event loop. ORM work belongs on a
        # separate synchronous thread, without disabling Django's safety guard.
        def run():
            try:
                return operation()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(run).result(timeout=30)

    def _exercise(self, browser, label):
        artifacts = Path(settings.BASE_DIR) / "test-artifacts"
        artifacts.mkdir(exist_ok=True)
        resident_context = browser.new_context(viewport={"width": 390, "height": 844})
        staff_context = browser.new_context(viewport={"width": 1280, "height": 900})
        contexts = [("resident", resident_context), ("staff", staff_context)]
        pages = {}
        errors = []
        for name, context in contexts:
            # Production Nginx serves this public asset outside Django. Mirror
            # that exact repository file on the isolated live-server test.
            context.route("**/ordzhonikidze-sunset.webp", lambda route: route.fulfill(
                path=str(Path(settings.BASE_DIR).parent / "ordzhonikidze-sunset.webp"), content_type="image/webp",
            ))
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            pages[name] = page
        resident, staff = pages["resident"], pages["staff"]
        try:
            self._login(resident, self.resident)
            self._login(staff, self.staff)
            resident.goto(f"{self.live_server_url}/admin/cabinet/account/{self.account.pk}/appeal/new/")
            resident.locator("#id_category").select_option(str(self.category.pk))
            resident.locator("#id_subject").fill(f"Contract {label}")
            resident.locator("#id_message").fill("Initial question")
            resident.get_by_role("button", name="Отправить обращение", exact=True).click()
            resident.wait_for_load_state("networkidle")
            appeal_id = self._db(lambda: ResidentAppeal.objects.get(subject=f"Contract {label}").pk)
            staff_url = f"{self.live_server_url}/work/appeals/{appeal_id}/"
            resident_url = f"{self.live_server_url}/admin/cabinet/account/{self.account.pk}/appeal/{appeal_id}/"
            staff.goto(staff_url)
            expect(staff.get_by_text("Initial question", exact=True)).to_be_visible()
            staff.locator("#id_body").fill("Please clarify")
            staff.locator("#id_next_status").select_option("awaiting_resident")
            staff.get_by_role("button", name="Отправить сообщение", exact=True).click()
            staff.wait_for_load_state("networkidle")
            resident.goto(f"{self.live_server_url}/admin/cabinet/account/{self.account.pk}/appeals/")
            expect(resident.get_by_text("Новый ответ", exact=True)).to_be_visible()
            resident.goto(resident_url)
            expect(resident.get_by_text("Please clarify", exact=True)).to_be_visible()
            resident.locator(".chat-compose #id_body").fill("Clarification")
            resident.get_by_role("button", name="Отправить", exact=True).click()
            resident.wait_for_load_state("networkidle")
            # Keep a real CSRF-bearing form open while the other role finishes.
            stale = resident_context.new_page()
            stale.goto(resident_url)
            stale.locator(".chat-compose #id_body").fill("Stale reply")
            staff.reload()
            expect(staff.get_by_text("Clarification", exact=True)).to_be_visible()
            staff.locator("#id_body").fill("Final answer")
            staff.locator("#id_next_status").select_option("resolved")
            staff.get_by_role("button", name="Отправить сообщение", exact=True).click()
            staff.wait_for_load_state("networkidle")
            staff.get_by_role("button", name="Закрыть обращение", exact=True).click()
            staff.wait_for_load_state("networkidle")
            expect(staff.get_by_role("heading", name="Диалог закрыт", exact=True)).to_be_visible()
            stale.get_by_role("button", name="Отправить", exact=True).click()
            stale.wait_for_load_state("networkidle")
            resident.reload()
            expect(resident.get_by_text("Final answer", exact=True)).to_have_count(1)
            expect(resident.locator(".resolved-bar")).to_contain_text("Вопрос завершён")
            expect(resident.locator(".chat-compose")).to_have_count(0)
            def snapshot():
                appeal = ResidentAppeal.objects.get(pk=appeal_id)
                return (
                    (appeal.status, appeal.response, appeal.responded_by_id),
                    list(appeal.history.order_by("history_date", "history_id").values_list("status", "history_user_id")),
                    list(appeal.resident_messages.values_list("author_id", "body")),
                    list(appeal.board_messages.values_list("author_id", "body")),
                )
            state, history, resident_messages, board_messages = self._db(snapshot)
            self.assertEqual(state, ("closed", "Final answer", self.staff.pk))
            self.assertEqual(history, [
                ("new", self.resident.pk), ("awaiting_resident", self.staff.pk),
                ("in_progress", self.resident.pk), ("resolved", self.staff.pk), ("closed", self.staff.pk),
            ])
            self.assertEqual(resident_messages, [(self.resident.pk, "Clarification")])
            self.assertEqual(board_messages, [(self.staff.pk, "Please clarify")])
            # One dual-role user, two tabs: visiting their personal UI leaves
            # the already-open staff tab and its endpoint authority intact.
            personal = staff_context.new_page()
            personal.goto(f"{self.live_server_url}/admin/cabinet/account/{self.account.pk}/")
            expect(personal.locator("body")).to_contain_text("Synthetic browser plot")
            staff.reload()
            expect(staff.get_by_role("heading", name="Диалог закрыт", exact=True)).to_be_visible()
            self._assert_accessibility(resident)
            self._assert_accessibility(staff)
            self.assertEqual(errors, [])
        finally:
            for name, context in contexts:
                try:
                    pages[name].screenshot(path=str(artifacts / f"contract-{label}-{name}.png"), full_page=True)
                    context.tracing.stop(path=str(artifacts / f"contract-{label}-{name}.zip"))
                finally:
                    context.close()

    def test_conversation_real_login_dual_tabs_chromium_and_webkit(self):
        with sync_playwright() as playwright:
            for label in ("chromium", "webkit"):
                with self.subTest(browser=label):
                    browser = getattr(playwright, label).launch(headless=True)
                    try:
                        self._exercise(browser, label)
                    finally:
                        browser.close()

    def _exercise_revocation(self, browser, label):
        def fixture():
            account = Account.objects.create(number=f"REVOKE-{label}", plot=f"Synthetic revocation {label}")
            person = ResidentIdentity.objects.get(user=self.resident).person
            ResidentAccess.objects.create(
                user=self.resident, account=account, role="owner", starts=timezone.localdate() - timedelta(days=60),
            )
            grant = PortalGrant.objects.create(
                person=person, account=account, starts=timezone.localdate() - timedelta(days=1),
                can_view_account=True, can_use_appeals=True, basis="Synthetic browser revocation", verified_by=self.staff,
            )
            return account.pk, grant.pk
        account_id, grant_id = self._db(fixture)
        artifacts = Path(settings.BASE_DIR) / "test-artifacts"
        artifacts.mkdir(exist_ok=True)
        contexts = [
            ("resident", browser.new_context(viewport={"width": 390, "height": 844})),
            ("staff", browser.new_context(viewport={"width": 1280, "height": 900})),
        ]
        pages = {}
        for name, context in contexts:
            context.route("**/ordzhonikidze-sunset.webp", lambda route: route.fulfill(
                path=str(Path(settings.BASE_DIR).parent / "ordzhonikidze-sunset.webp"), content_type="image/webp",
            ))
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            pages[name] = context.new_page()
        resident, staff = pages["resident"], pages["staff"]
        try:
            self._login(resident, self.resident)
            self._login(staff, self.staff)
            resident.goto(f"{self.live_server_url}/admin/cabinet/account/{account_id}/appeal/new/")
            resident.locator("#id_category").select_option(str(self.category.pk))
            resident.locator("#id_subject").fill(f"Revocation {label}")
            resident.locator("#id_message").fill("Before revocation")
            resident.get_by_role("button", name="Отправить обращение", exact=True).click()
            resident.wait_for_load_state("networkidle")
            appeal_id = self._db(lambda: ResidentAppeal.objects.get(subject=f"Revocation {label}").pk)
            resident_url = f"{self.live_server_url}/admin/cabinet/account/{account_id}/appeal/{appeal_id}/"
            staff.goto(f"{self.live_server_url}/work/appeals/{appeal_id}/")
            staff.locator("#id_body").fill("Please clarify before revocation")
            staff.locator("#id_next_status").select_option("awaiting_resident")
            staff.get_by_role("button", name="Отправить сообщение", exact=True).click()
            staff.wait_for_load_state("networkidle")
            resident.goto(resident_url)
            resident.locator(".chat-compose #id_body").fill("Stale reply after revocation")
            staff.goto(f"{self.live_server_url}/work/access/grants/{grant_id}/")
            staff.locator("#id_ends_on").fill(timezone.localdate().isoformat())
            staff.get_by_role("button", name="Завершить доступ", exact=True).click()
            staff.wait_for_load_state("networkidle")
            self.assertEqual(self._db(lambda: PortalGrant.objects.get(pk=grant_id).ends), timezone.localdate())
            with resident.expect_response(lambda response: response.url == resident_url and response.request.method == "POST") as denied:
                resident.get_by_role("button", name="Отправить", exact=True).click()
            self.assertEqual(denied.value.status, 404)
            resident.wait_for_load_state("networkidle")
            self.assertEqual(resident.goto(f"{self.live_server_url}/admin/cabinet/account/{account_id}/").status, 404)
            self.assertEqual(resident.goto(f"{self.live_server_url}/admin/cabinet/account/{self.account.pk}/").status, 200)
            def snapshot():
                appeal = ResidentAppeal.objects.get(pk=appeal_id)
                return (
                    appeal.resident_messages.count(), appeal.history.count(),
                    (appeal.author_id, appeal.account_id, appeal.message, appeal.status),
                    ResidentAccess.objects.get(user=self.resident, account_id=account_id).ends,
                )
            self.assertEqual(self._db(snapshot), (0, 2, (self.resident.pk, account_id, "Before revocation", "awaiting_resident"), None))
            self._assert_accessibility(staff)
        finally:
            for name, context in contexts:
                try:
                    pages[name].screenshot(path=str(artifacts / f"contract-revocation-{label}-{name}.png"), full_page=True)
                    context.tracing.stop(path=str(artifacts / f"contract-revocation-{label}-{name}.zip"))
                finally:
                    context.close()

    def test_revoked_grant_blocks_legacy_open_form_chromium_and_webkit(self):
        with sync_playwright() as playwright:
            for label in ("chromium", "webkit"):
                with self.subTest(browser=label):
                    browser = getattr(playwright, label).launch(headless=True)
                    try:
                        self._exercise_revocation(browser, label)
                    finally:
                        browser.close()
