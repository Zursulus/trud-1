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

    def test_legacy_work_route_remains_available_but_is_not_primary_navigation(self):
        self.client.force_login(self.manager)

        response = self.client.get("/work/tasks/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "water/work/tasks.html")
        self.assertContains(response, "Рабочая очередь")
        self.assertContains(response, 'href="/work/documents/"')
        self.assertNotContains(response, '>Работа</a>')

        home = self.client.get("/work/")
        self.assertEqual(home.status_code, 200)
        self.assertNotContains(home, 'href="/work/tasks/"')
        self.assertContains(home, 'href="/work/more/"')
