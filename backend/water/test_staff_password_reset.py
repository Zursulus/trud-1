from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .management.commands.setup_roles import ADMIN, CONTROLLER, OPERATOR
from .models import ResidentPasswordReset, User


class StaffPasswordResetTests(TestCase):
    def setUp(self):
        call_command('setup_roles', verbosity=0)
        self.manager = User.objects.create_user(
            username='password-reset-manager', password='manager-password-2026!', is_staff=True,
        )
        self.manager.groups.add(Group.objects.get(name=ADMIN))
        self.controller = User.objects.create_user(
            username='scoped-controller-test', password='old-controller-password-2026!', is_staff=True,
        )
        self.controller.groups.add(Group.objects.get(name=CONTROLLER))

    def test_administrator_can_issue_one_time_reset_for_controller(self):
        self.client.force_login(self.manager)
        url = reverse('water_password_access_admin')
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {'user_id': self.controller.pk})
        self.assertEqual(response.status_code, 200)
        reset_url = response.context['reset_url']
        self.assertIn('/admin/cabinet/reset/', reset_url)
        self.assertEqual(ResidentPasswordReset.objects.filter(user=self.controller).count(), 1)
        self.assertEqual(ResidentPasswordReset.objects.get(user=self.controller).history.first().history_user, self.manager)

        raw = reset_url.rstrip('/').split('/')[-1]
        self.client.logout()
        new_password = 'Tangerine-Orbit-Quartz-582!'
        response = self.client.post(
            reverse('resident_password_reset', args=[raw]),
            {'password1': new_password, 'password2': new_password},
        )
        self.assertRedirects(response, reverse('admin:login'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.controller.refresh_from_db()
        self.assertTrue(self.controller.check_password(new_password))
        reset = ResidentPasswordReset.objects.get(user=self.controller)
        self.assertIsNotNone(reset.used_at)
        self.assertEqual(self.client.get(reverse('resident_password_reset', args=[raw])).status_code, 410)

    def test_new_reset_revokes_previous_link(self):
        self.client.force_login(self.manager)
        url = reverse('water_password_access_admin')
        first = self.client.post(url, {'user_id': self.controller.pk}).context['reset_url'].rstrip('/').split('/')[-1]
        second = self.client.post(url, {'user_id': self.controller.pk}).context['reset_url'].rstrip('/').split('/')[-1]
        self.assertNotEqual(first, second)
        self.assertEqual(self.client.get(reverse('resident_password_reset', args=[first])).status_code, 410)
        self.assertEqual(self.client.get(reverse('resident_password_reset', args=[second])).status_code, 200)

    def test_operator_cannot_issue_staff_reset(self):
        operator = User.objects.create_user(
            username='password-reset-operator', password='operator-password-2026!', is_staff=True,
        )
        operator.groups.add(Group.objects.get(name=OPERATOR))
        self.client.force_login(operator)
        url = reverse('water_password_access_admin')
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_reset_cannot_target_superuser_or_unrelated_staff(self):
        superuser = User.objects.create_superuser(
            username='password-reset-root', password='root-password-2026!', email='root@example.test',
        )
        unrelated = User.objects.create_user(
            username='unrelated-staff', password='unrelated-password-2026!', is_staff=True,
        )
        self.client.force_login(self.manager)
        url = reverse('water_password_access_admin')
        for target in (superuser, unrelated):
            self.assertEqual(self.client.post(url, {'user_id': target.pk}).status_code, 403)

    def test_administrator_still_cannot_open_user_admin(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse('admin:water_user_changelist')).status_code, 403)
