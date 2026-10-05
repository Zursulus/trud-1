"""Account viewers do not enumerate water nodes to discover forbidden actions."""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from .access_control import AccessAssignment
from .models import Account, Person, SupplyNode, User
from .resident_models import ResidentIdentity


class AccountBindingQueryTests(TestCase):
    def test_v2_account_viewer_query_count_does_not_grow_with_unmanaged_nodes(self):
        account = Account.objects.create(number='QUERY-VIEW-1', plot='Synthetic query plot')
        viewer = User.objects.create_user(username='query-account-viewer', is_staff=True)
        verifier = User.objects.create_user(username='query-viewer-verifier', is_staff=True)
        person = Person.objects.create(full_name='Synthetic account-only viewer')
        ResidentIdentity.objects.create(
            user=viewer, person=person, verified_by=verifier, basis='Synthetic identity',
        )
        AccessAssignment.objects.create(
            person=person, role_code='test-account-only-viewer', role_version=1,
            role_label='Account-only viewer',
            allowed_capabilities=['accounts.view'], capabilities=['accounts.view'],
            scope_type='account', scope_object_id=account.pk,
            starts=timezone.localdate(), basis='Synthetic account scope', granted_by=verifier,
        )
        SupplyNode.objects.create(name='Synthetic query node 0')
        self.client.force_login(viewer)
        url = reverse('staff_workspace:account', args=[account.pk])
        with CaptureQueriesContext(connection) as one_node_queries:
            first = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.context['can_bind_meter'])

        for index in range(1, 31):
            SupplyNode.objects.create(name=f'Synthetic query node {index}')
        with CaptureQueriesContext(connection) as many_node_queries:
            second = self.client.get(url)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(second.context['can_bind_meter'])
        self.assertNotContains(second, 'Привязать счётчик')
        self.assertLessEqual(
            len(many_node_queries), len(one_node_queries) + 2,
            f'Account-only viewer queries grew with nodes: '
            f'{len(one_node_queries)} -> {len(many_node_queries)}',
        )
        self.assertFalse(any('water_supplynode' in query['sql'].lower() for query in many_node_queries))
