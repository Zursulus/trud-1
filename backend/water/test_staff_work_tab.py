from io import StringIO

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
