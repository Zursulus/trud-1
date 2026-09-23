"""Compatibility wrapper around the historical core test suite.

The original suite stays byte-for-byte intact in _legacy_suite.py. ZUR-59 only
updates the few tests whose old expectations intentionally conflict with the new
private-registry permission boundary.
"""

from ._legacy_suite import *  # noqa: F401,F403
from . import _legacy_suite as legacy

from django.contrib.auth.models import Group


class RoleAuditTests(legacy.RoleAuditTests):
    def test_legacy_admin_role_is_renamed_without_losing_members(self):
        legacy_group = Group.objects.create(name='Администратор СНТ')
        legacy_user = User.objects.create_user(username='legacy-manager', is_staff=True)
        legacy_user.groups.add(legacy_group)

        call_command('setup_roles', stdout=StringIO())

        legacy_user.refresh_from_db()
        self.assertFalse(Group.objects.filter(name='Администратор СНТ').exists())
        self.assertTrue(legacy_user.groups.filter(name='Администратор ТСН').exists())
        self.assertFalse(legacy_user.has_perm('water.change_person'))
        self.assertFalse(legacy_user.has_perm('water.access_private_registry'))


class RegistryTests(legacy.RegistryTests):
    def setUp(self):
        super().setUp()
        self.private_manager = User.objects.create_user(username='private-registry-manager', is_staff=True)
        self.private_manager.groups.add(
            Group.objects.get(name='Администратор ТСН'),
            Group.objects.get(name='Закрытый реестр членов ТСН'),
        )

    def test_private_registry_user_can_register_relations_and_others_cannot_view(self):
        self.login_as(self.manager)
        self.assertEqual(self.client.get('/admin/water/person/').status_code, 403)
        self.assertEqual(self.client.get('/admin/water/plotrelation/').status_code, 403)
        self.assertEqual(self.client.get('/admin/water/landplot/').status_code, 200)

        self.login_as(self.private_manager)
        response = self.client.post('/admin/water/plotrelation/add/', {
            'person': self.person.pk, 'plot': self.plot.pk, 'role': 'owner',
            'starts': '2025-01-01', 'version': 0,
        })
        self.assertEqual(response.status_code, 302)
        relation = PlotRelation.objects.get()
        self.assertEqual(relation.history.first().history_user, self.private_manager)
        self.assertEqual(self.client.get('/admin/water/person/').status_code, 200)
        self.assertEqual(self.client.get('/admin/water/landplot/').status_code, 200)
        self.assertEqual(self.client.get('/admin/water/plotrelation/').status_code, 200)

        self.login_as(self.operator)
        for url in ['/admin/water/person/', '/admin/water/landplot/', '/admin/water/plotrelation/']:
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_setup_roles_separates_private_registry_from_operations(self):
        self.assertFalse(self.manager.has_perm('water.change_person'))
        self.assertFalse(self.manager.has_perm('water.view_historicalplotrelation'))
        self.assertFalse(self.manager.has_perm('water.access_private_registry'))
        self.assertTrue(self.private_manager.has_perm('water.change_person'))
        self.assertTrue(self.private_manager.has_perm('water.view_historicalplotrelation'))
        self.assertTrue(self.private_manager.has_perm('water.access_private_registry'))
        self.assertFalse(self.operator.has_perm('water.view_person'))
        self.assertFalse(self.operator.has_perm('water.add_landplot'))

    # Disable the two historical expectations replaced above. They are kept in
    # _legacy_suite.py for audit/history but are no longer the intended policy.
    test_manager_can_register_relations_with_actor_and_operator_cannot_view = None
    test_setup_roles_does_not_grant_registry_to_operator = None


class SafeImportTests(legacy.SafeImportTests):
    def test_private_registry_authorization_is_required_for_pii_import(self):
        call_command('setup_roles', stdout=StringIO())
        manager = User.objects.create_user(username='import-manager', is_staff=True)
        manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        authorized = User.objects.create_user(username='private-import-manager', is_staff=True)
        authorized.groups.add(
            Group.objects.get(name='Администратор ТСН'),
            Group.objects.get(name='Закрытый реестр членов ТСН'),
        )
        operator = User.objects.create_user(username='import-operator', is_staff=True)
        operator.groups.add(Group.objects.get(name='Оператор воды'))
        url = '/admin/water/importbatch/upload/'

        self.login_as(operator)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.login_as(manager)
        self.assertEqual(self.client.get(url).status_code, 403)

        self.login_as(authorized)
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {
            'file': self.csv_upload(), 'effective_date': '2026-01-01', 'notes': 'Первичная сверка',
        })
        self.assertEqual(response.status_code, 302)
        batch = ImportBatch.objects.get()
        self.assertEqual(batch.notes, 'Первичная сверка')
        self.assertEqual(batch.history.first().history_user, authorized)

    test_manager_upload_page_and_operator_isolation = None
