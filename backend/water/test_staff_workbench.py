from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .access_control import assignment_from_role, revoke_assignment
from .access_policy import ScopeType
from .models import Account, LandPlot, Meter, Person, PlotRelation, ResidentAccess, SupplyNode, User
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity


class WorkbenchTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.admin = User.objects.create_user(username='panel-admin', is_staff=True, is_superuser=True)
        self.user = User.objects.create_user(username='panel-resident')
        self.person = Person.objects.create(full_name='Синтетический житель')
        ResidentIdentity.objects.create(user=self.user, person=self.person, verified_by=self.admin, basis='Тест')
        self.account = Account.objects.create(number='PANEL-TEST', plot='Тестовая улица 2')
        self.plot = LandPlot.objects.create(label='Тестовый участок', address='Тестовая улица 2', account=self.account)
        PlotRelation.objects.create(person=self.person, plot=self.plot, role='owner', starts=self.today, document='Тестовый документ')
        ResidentAccess.objects.create(user=self.user, account=self.account, role='payer', starts=self.today)
        self.grant = PortalGrant.objects.create(person=self.person, account=self.account, starts=self.today,
            basis='Тестовое основание', verified_by=self.admin, can_view_account=True, can_submit_water=True)
        self.node = SupplyNode.objects.create(name='Тестовый узел')
        self.meter = Meter.objects.create(serial='PANEL-METER', kind='individual', node=self.node, account=self.account)
        self.client.force_login(self.admin)

    def test_real_links_and_read_only(self):
        for kind, obj in [('account', self.account), ('plot', self.plot), ('person', self.person)]:
            with CaptureQueriesContext(connection) as queries:
                response = self.client.get('/work/panel/', {'kind': kind, 'id': obj.pk})
            self.assertEqual(response.status_code, 200)
            for text in ['PANEL-METER', 'Тестовый документ', 'PortalGrant', 'ResidentAccess']:
                self.assertContains(response, text)
            self.assertIn('no-store', response['Cache-Control'])
            self.assertIn('noindex', response['X-Robots-Tag'])
            writes = [q['sql'] for q in queries if q['sql'].lstrip().split()[0].upper() in {'INSERT', 'UPDATE', 'DELETE'}]
            self.assertEqual(writes, [])
        self.assertEqual(self.client.post('/work/panel/').status_code, 405)

    def test_access_denied(self):
        self.client.logout()
        self.assertEqual(self.client.get('/work/panel/').status_code, 302)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/work/panel/').status_code, 302)
        staff = User.objects.create_user(username='plain-staff', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get('/work/panel/').status_code, 403)
        self.assertNotContains(self.client.get('/work/more/'), 'href="/work/panel/"')

    def test_v2_admin_and_revocation(self):
        person = Person.objects.create(full_name='Тестовый администратор')
        user = User.objects.create_user(username='v2-panel-admin')
        ResidentIdentity.objects.create(user=user, person=person, verified_by=self.admin, basis='Тест')
        assignment = assignment_from_role(person=person, role_code='tsn_admin', scope_type=ScopeType.ALL,
            starts=self.today, basis='Тест', granted_by=self.admin)
        user.refresh_from_db()
        self.client.force_login(user)
        self.assertEqual(self.client.get('/work/panel/').status_code, 200)
        revoke_assignment(assignment, actor=self.admin, reason='Тестовый отзыв')
        self.assertIn(self.client.get('/work/panel/').status_code, (302, 403))

    def test_search_validation_and_escape(self):
        for query in ('panel-resident', 'Тестовая улица', '9' * 160):
            self.assertEqual(self.client.get('/work/panel/', {'q': query}).status_code, 200)
        self.assertContains(self.client.get('/work/panel/', {'q': 'panel-resident'}), self.person.full_name)
        for params in ({'kind': 'bogus', 'id': '1'}, {'kind': 'person', 'id': '9' * 160}, {'kind': 'person', 'id': '²'}):
            self.assertEqual(self.client.get('/work/panel/', params).status_code, 400)
        self.assertEqual(self.client.get('/work/panel/', {'kind': 'person', 'id': '999999999'}).status_code, 404)
        self.person.full_name = '<script>alert(1)</script>'
        self.person.save()
        response = self.client.get('/work/panel/', {'kind': 'person', 'id': self.person.pk})
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;')

    def test_unlinked_plot_does_not_show_other_meters(self):
        plot = LandPlot.objects.create(label='Без счёта')
        response = self.client.get('/work/panel/', {'kind': 'plot', 'id': plot.pk})
        self.assertContains(response, 'Связанный лицевой счёт не указан')
        self.assertNotContains(response, 'PANEL-METER')

    def test_expired_grant_excluded_and_unknown_legacy_identity_visible(self):
        self.grant.starts = self.today - timedelta(days=2)
        self.grant.ends = self.today
        self.grant.save()
        other = User.objects.create_user(username='unlinked-login')
        ResidentAccess.objects.create(user=other, account=self.account, role='payer', starts=self.today)
        response = self.client.get('/work/panel/', {'kind': 'account', 'id': self.account.pk})
        self.assertNotContains(response, 'Тестовое основание')
        self.assertContains(response, 'Связь с человеком не подтверждена')
        self.assertContains(response, 'ResidentAccess')

    def test_role_scope_does_not_invent_meter_group(self):
        assignment_from_role(person=self.person, role_code='controller', scope_type=ScopeType.SUPPLY_NODE,
            scope_object_id=self.node.pk, starts=self.today, basis='Тестовое назначение', granted_by=self.admin)
        response = self.client.get('/work/panel/', {'kind': 'account', 'id': self.account.pk})
        self.assertContains(response, 'Тестовое назначение')
        self.assertContains(response, 'Линия: не указана')

    def test_bounded_search(self):
        for n in range(32):
            Account.objects.create(number=f'BOUND-{n}', plot='Ограниченный поиск')
        response = self.client.get('/work/panel/', {'q': 'Ограниченный поиск'})
        self.assertTrue(response.context['search_truncated'])
        self.assertEqual(len(response.context['results']), 30)
