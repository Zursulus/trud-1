from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from .models import User


class StaffWorkTabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.manager = User.objects.create_user(username="work-tab-manager", is_staff=True)
        cls.manager.groups.add(Group.objects.get(name="Администратор ТСН"))

    def test_work_tab_has_dedicated_route_and_active_state(self):
        self.client.force_login(self.manager)

        response = self.client.get("/work/tasks/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "water/work/tasks.html")
        self.assertContains(response, "Рабочая очередь")
        self.assertContains(response, 'href="/work/tasks/" aria-current="page"')
        self.assertContains(response, 'href="/work/documents/"')

        home = self.client.get("/work/")
        self.assertEqual(home.status_code, 200)
        self.assertContains(home, 'href="/work/tasks/"')

    def test_internal_roles_guide_is_staff_only_and_linked_from_more(self):
        anonymous = self.client.get("/work/more/roles-guide/")
        self.assertEqual(anonymous.status_code, 302)

        with TemporaryDirectory() as temp_dir:
            guide = Path(temp_dir) / "roles-guide-current.pdf"
            version = Path(temp_dir) / "roles-guide-version.txt"
            guide.write_bytes(b"%PDF-1.4\n% test guide\n")
            version.write_text("1.0", encoding="utf-8")

            with patch("water.staff_more.ROLES_GUIDE_FILE", guide), patch(
                "water.staff_more.ROLES_GUIDE_VERSION_FILE", version
            ):
                self.client.force_login(self.manager)
                more = self.client.get("/work/more/")
                self.assertEqual(more.status_code, 200)
                self.assertContains(more, "Роли и полномочия")
                self.assertContains(more, "версия 1.0")
                self.assertContains(more, 'href="/work/more/roles-guide/"')

                response = self.client.get("/work/more/roles-guide/")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/pdf")
                self.assertEqual(response["Cache-Control"], "private, no-store")
