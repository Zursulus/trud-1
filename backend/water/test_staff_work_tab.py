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

    def test_internal_roles_guide_is_staff_only_and_linked_from_more(self):
        anonymous = self.client.get("/work/more/roles-guide/")
        self.assertEqual(anonymous.status_code, 302)

        self.client.force_login(self.manager)
        more = self.client.get("/work/more/")
        self.assertEqual(more.status_code, 200)
        self.assertContains(more, "Роли и полномочия")
        self.assertContains(more, "версия 1.0")
        self.assertContains(more, 'href="/work/more/roles-guide/"')

        guide = self.client.get("/work/more/roles-guide/")
        self.assertEqual(guide.status_code, 200)
        self.assertTemplateUsed(guide, "water/work/roles_guide.html")
        self.assertContains(guide, "17 рабочих ролей")
        self.assertContains(guide, "Председатель не выбирается")
        self.assertContains(guide, "Администратор ТСН")
