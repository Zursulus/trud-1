from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from ._test_auth import login_with_admin_otp
from .models import Account, User
from .staff_db_map import build_schema


class DbMapTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', stdout=StringIO())
        cls.staff = User.objects.create_user(username='dbmap-staff', is_staff=True)
        cls.admin = User.objects.create_user(username='dbmap-manager', is_staff=True)
        cls.admin.groups.add(Group.objects.get(name='Администратор ТСН'))
        cls.superuser = User.objects.create_user(username='dbmap-root', is_staff=True, is_superuser=True)
        cls.marker = 'PRIVATE-DBMAP-871c2af958'
        Account.objects.create(number='DBMAP-1', contact_name=cls.marker)

    def test_anonymous_redirects_to_login(self):
        response = self.client.get(reverse('staff_workspace:db_map'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_unprivileged_staff_forbidden_and_menu_hidden(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('staff_workspace:db_map')).status_code, 403)
        self.assertNotContains(self.client.get(reverse('staff_workspace:more')), reverse('staff_workspace:db_map'))

    def test_admin_sees_structure_without_resident_rows(self):
        login_with_admin_otp(self.client, self.admin)
        response = self.client.get(reverse('staff_workspace:db_map'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.marker)
        self.assertContains(response, 'water.Account')
        self.assertContains(response, 'water.Reading')
        schema = response.context['db_schema']
        self.assertTrue(any(r['source'] == 'water.Reading' and r['target'] == 'water.Meter'
                            and r['kind'] == 'fk' and r['field'] == 'meter' for r in schema['relations']))
        self.assertContains(self.client.get(reverse('staff_workspace:more')), reverse('staff_workspace:db_map'))

    def test_superuser_allowed_and_only_get(self):
        login_with_admin_otp(self.client, self.superuser)
        url = reverse('staff_workspace:db_map')
        self.assertEqual(self.client.get(url).status_code, 200)
        for method in ('post', 'put', 'patch', 'delete', 'head'):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.client, method)(url).status_code, 405)

    def test_schema_uses_metadata_without_queries(self):
        with self.assertNumQueries(0):
            schema = build_schema()
        nodes = {node['id']: node for node in schema['nodes']}
        self.assertIn('water.User', nodes)  # This project's replacement for auth.User.
        self.assertEqual(nodes['sessions.Session']['section'], 'service')
        self.assertEqual(nodes['admin.LogEntry']['section'], 'service')
        historical = [n for n in schema['nodes'] if n['model'].startswith('Historical')]
        self.assertTrue(historical)
        self.assertTrue(all(n['section'] == 'service' for n in historical))
        self.assertTrue(any(r['kind'] == 'm2m' for r in schema['relations']))
        self.assertTrue(any(r['kind'] == 'o2o' for r in schema['relations']))
        self.assertEqual(nodes['water.Account']['section'], 'residents')
        field = next(f for f in nodes['water.Reading']['fields'] if f['name'] == 'meter')
        self.assertEqual(field['type'], 'ForeignKey')
        self.assertTrue(field['required'])
