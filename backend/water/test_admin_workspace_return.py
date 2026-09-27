from django.test import TestCase
from django.urls import reverse

from .models import User


class AdminWorkspaceReturnLinkTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="staff-nav",
            password="not-used-in-test",
            is_staff=True,
        )

    def test_admin_header_has_staff_workspace_return_link(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'href="/work/"')
        self.assertContains(response, "Вернуться в Рабочую базу")
