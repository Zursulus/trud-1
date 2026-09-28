from datetime import timedelta
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .access_control import AccessAssignment, AccessDelegation, assignment_from_role, link_identity
from .access_scope import ScopeRef
from .access_policy import ScopeType
from .access_resolver import can
from .board_polls import BoardMembership, BoardPoll, BoardQuestion, BoardVote
from .models import Account, ControllerReadingSubmission, Membership, Meter, Person, SupplyNode, User, WaterGroup
from .portal_permissions import PortalGrant, resolved_access_at, CAP_FINANCE, CAP_VIEW_ACCOUNT


class StaffAccessV2Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', stdout=StringIO())
        cls.dual = User.objects.create_user(username='v2-dual', is_staff=True)
        cls.dual.groups.add(
            Group.objects.get(name='Администратор ТСН'),
            Group.objects.get(name='Закрытый реестр членов ТСН'),
        )
        cls.node = SupplyNode.objects.create(name='V2 узел')
        cls.group = WaterGroup.objects.create(name='V2 линия', node=cls.node)
        cls.account = Account.objects.create(number='V2-001', plot='V2 дом 1')
        Membership.objects.create(account=cls.account, group=cls.group, starts=timezone.localdate() - timedelta(days=10))

    def _person_with_login(self, name='Иван Тестов', username='ivan-v2'):
        person = Person.objects.create(full_name=name, email=f'{username}@example.test')
        user = User.objects.create_user(username=username, email=f'{username}@example.test')
        link_identity(user=user, person=person, actor=self.dual, basis='Синтетическая проверка')
        return person, user

    def test_directory_and_person_card_show_combined_person_context(self):
        person, user = self._person_with_login()
        PortalGrant.objects.create(
            person=person, account=self.account, starts=timezone.localdate(),
            basis='Личный доступ', verified_by=self.dual, can_view_account=True,
            can_submit_water=True,
        )
        self.client.force_login(self.dual)
        response = self.client.post(
            f'/work/access/people/{person.pk}/',
            {
                'action': 'assign',
                'assign-role_code': 'line_senior',
                'assign-scope_type': 'water_group',
                'assign-water_group': self.group.pk,
                'assign-starts': timezone.localdate().isoformat(),
                'assign-basis': 'Назначен старшим линии',
                'assign-notes': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        assignment = AccessAssignment.objects.get(person=person, role_code='line_senior')
        self.assertEqual(assignment.scope_object_id, self.group.pk)
        user.refresh_from_db()
        self.assertTrue(user.is_staff)

        page = self.client.get(f'/work/access/people/{person.pk}/')
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Старший линии')
        self.assertContains(page, self.group.name)
        self.assertContains(page, self.account.number)
        self.assertContains(page, 'Подотчётных счетов сегодня: 1')

        directory = self.client.get('/work/access/people/?q=Иван')
        self.assertEqual(directory.status_code, 200)
        self.assertContains(directory, person.full_name)
        self.assertContains(directory, 'Житель')
        self.assertContains(directory, 'Старший линии')

    def test_same_login_is_resident_and_service_user(self):
        person, user = self._person_with_login('Смешанная Роль', 'mixed-v2')
        PortalGrant.objects.create(
            person=person, account=self.account, starts=timezone.localdate(), basis='Личный доступ',
            verified_by=self.dual, can_view_account=True, can_view_finance=True,
        )
        assignment_from_role(
            person=person, role_code='line_senior', scope_type=ScopeType.WATER_GROUP,
            scope_object_id=self.group.pk, basis='Старший линии', granted_by=self.dual,
        )
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.client.force_login(user)
        portal = self.client.get('/admin/cabinet/')
        self.assertEqual(portal.status_code, 200)
        self.assertContains(portal, self.account.plot)
        self.assertTrue(can(user, 'water.observation.review_line', scope=ScopeRef(ScopeType.WATER_GROUP, self.group.pk)))

    def test_line_senior_and_controller_are_independent_on_same_line(self):
        senior, _ = self._person_with_login('Старший Линии', 'senior-v2')
        controller, _ = self._person_with_login('Контролёр Линии', 'controller-v2')
        assignment_from_role(
            person=senior, role_code='line_senior', scope_type=ScopeType.WATER_GROUP,
            scope_object_id=self.group.pk, basis='Старший', granted_by=self.dual,
        )
        assignment_from_role(
            person=controller, role_code='controller', scope_type=ScopeType.WATER_GROUP,
            scope_object_id=self.group.pk, basis='Контролёр', granted_by=self.dual,
        )
        self.assertEqual(AccessAssignment.objects.filter(scope_object_id=self.group.pk).count(), 2)

    def test_delegated_resident_right_is_real_in_portal(self):
        delegator, _ = self._person_with_login('Передающий', 'delegator-v2')
        delegate, delegate_user = self._person_with_login('Представитель', 'delegate-v2')
        PortalGrant.objects.create(
            person=delegator, account=self.account, starts=timezone.localdate() - timedelta(days=1),
            basis='Собственное право', verified_by=self.dual,
            can_view_account=True, can_view_finance=True,
        )
        self.client.force_login(self.dual)
        response = self.client.post(
            f'/work/access/people/{delegator.pk}/',
            {
                'action': 'delegate',
                'delegate-delegate': delegate.pk,
                'delegate-account': self.account.pk,
                'delegate-capabilities': ['resident.account.view', 'resident.finance.view'],
                'delegate-starts': timezone.localdate().isoformat(),
                'delegate-basis': 'Проверенная доверенность',
                'delegate-notes': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AccessDelegation.objects.filter(delegator=delegator, delegate=delegate).count(), 1)
        self.assertIsNotNone(resolved_access_at(delegate_user, self.account.pk, CAP_VIEW_ACCOUNT))
        self.assertIsNotNone(resolved_access_at(delegate_user, self.account.pk, CAP_FINANCE))
        self.client.force_login(delegate_user)
        self.assertEqual(self.client.get('/admin/cabinet/').status_code, 200)

    def test_special_delegation_requires_base_account_right(self):
        delegator, _ = self._person_with_login('Передающий 2', 'delegator2-v2')
        delegate, _ = self._person_with_login('Представитель 2', 'delegate2-v2')
        PortalGrant.objects.create(
            person=delegator, account=self.account, starts=timezone.localdate(),
            basis='Собственное право', verified_by=self.dual,
            can_view_account=True, can_view_finance=True,
        )
        self.client.force_login(self.dual)
        response = self.client.post(
            f'/work/access/people/{delegator.pk}/',
            {
                'action': 'delegate',
                'delegate-delegate': delegate.pk,
                'delegate-account': self.account.pk,
                'delegate-capabilities': ['resident.finance.view'],
                'delegate-starts': timezone.localdate().isoformat(),
                'delegate-basis': 'Неполная передача',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'только вместе с базовым доступом')
        self.assertFalse(AccessDelegation.objects.filter(delegator=delegator).exists())

    def test_v2_access_admin_can_manage_without_private_contact_visibility(self):
        manager_person, manager_user = self._person_with_login('Менеджер Доступов', 'manager-v2')
        target, _ = self._person_with_login('Скрытые Контакты', 'hidden-v2')
        assignment_from_role(
            person=manager_person, role_code='access_admin', scope_type=ScopeType.ALL,
            basis='Управление доступами', granted_by=self.dual,
        )
        manager_user.refresh_from_db()
        self.assertTrue(manager_user.is_staff)
        self.client.force_login(manager_user)
        response = self.client.get('/work/access/people/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, target.full_name)
        self.assertNotContains(response, target.email)

    def test_revoked_assignment_does_not_authorize_future_delegation_or_block_reassignment(self):
        person, _ = self._person_with_login('Повторное Назначение', 'repeat-v2')
        first = assignment_from_role(
            person=person, role_code='line_senior', scope_type=ScopeType.WATER_GROUP,
            scope_object_id=self.group.pk, basis='Первое', granted_by=self.dual,
        )
        from .access_control import revoke_assignment
        revoke_assignment(first, actor=self.dual, reason='Смена старшего')
        second = assignment_from_role(
            person=person, role_code='line_senior', scope_type=ScopeType.WATER_GROUP,
            scope_object_id=self.group.pk, basis='Повторное', granted_by=self.dual,
        )
        self.assertNotEqual(first.pk, second.pk)
        self.assertIsNotNone(first.revoked_at)


    def test_line_senior_assignment_opens_only_line_senior_workspace(self):
        person, user = self._person_with_login('Рабочий Старший', 'work-senior-v2')
        other_group = WaterGroup.objects.create(name='V2 чужая линия', node=self.node)
        other_account = Account.objects.create(number='V2-OTHER', plot='V2 чужой дом')
        Membership.objects.create(account=other_account, group=other_group, starts=timezone.localdate())
        assignment_from_role(
            person=person, role_code='line_senior', scope_type=ScopeType.WATER_GROUP,
            scope_object_id=self.group.pk, basis='Рабочий старший', granted_by=self.dual,
        )
        user.refresh_from_db()
        self.client.force_login(user)
        water = self.client.get('/work/water/')
        self.assertEqual(water.status_code, 200)
        self.assertContains(water, 'Мои линии')
        workspace = self.client.get('/admin/water/controller-workspace/')
        self.assertEqual(workspace.status_code, 200)
        self.assertContains(workspace, self.group.name)
        self.assertNotContains(workspace, other_group.name)

    def test_controller_assignment_opens_scoped_observation_not_line_senior_workspace(self):
        person, user = self._person_with_login('Рабочий Контролёр', 'work-controller-v2')
        meter = Meter.objects.create(
            serial='V2-CTRL-1', kind='individual', node=self.node, account=self.account,
        )
        other_group = WaterGroup.objects.create(name='V2 контроль чужой', node=self.node)
        other_account = Account.objects.create(number='V2-CTRL-OTHER', plot='V2 контроль чужой дом')
        Membership.objects.create(account=other_account, group=other_group, starts=timezone.localdate())
        other_meter = Meter.objects.create(
            serial='V2-CTRL-2', kind='individual', node=self.node, account=other_account,
        )
        assignment_from_role(
            person=person, role_code='controller', scope_type=ScopeType.WATER_GROUP,
            scope_object_id=self.group.pk, basis='Независимый контроль', granted_by=self.dual,
        )
        user.refresh_from_db()
        self.client.force_login(user)
        water = self.client.get('/work/water/')
        self.assertEqual(water.status_code, 200)
        self.assertContains(water, 'Передать показание на проверку')
        self.assertNotContains(water, 'Внести и сверить показания')
        self.assertEqual(self.client.get('/admin/water/controller-workspace/').status_code, 403)
        capture = self.client.get('/admin/water/controllerreadingsubmission/capture/')
        self.assertEqual(capture.status_code, 200)
        choices = capture.context['form'].fields['meter'].queryset
        self.assertTrue(choices.filter(pk=meter.pk).exists())
        self.assertFalse(choices.filter(pk=other_meter.pk).exists())

    def test_v2_access_admin_can_use_main_access_workspace_and_create_person(self):
        manager_person, manager_user = self._person_with_login('Полный Менеджер', 'full-manager-v2')
        assignment_from_role(
            person=manager_person, role_code='access_admin', scope_type=ScopeType.ALL,
            basis='Управление доступами', granted_by=self.dual,
        )
        manager_user.refresh_from_db()
        self.client.force_login(manager_user)
        self.assertEqual(self.client.get('/work/access/').status_code, 200)
        created = self.client.post('/work/access/people/new/', {
            'full_name': 'Создан V2 менеджером',
            'email': 'created-by-v2@example.test',
            'phone': '', 'notes': '',
        })
        self.assertEqual(created.status_code, 302)
        self.assertTrue(Person.objects.filter(email='created-by-v2@example.test').exists())

    def test_access_admin_can_toggle_resident_features_without_recreating_grant(self):
        manager_person, manager_user = self._person_with_login('Менеджер Прав', 'rights-manager-v2')
        target, _ = self._person_with_login('Житель с настройками', 'rights-resident-v2')
        assignment_from_role(
            person=manager_person, role_code='access_admin', scope_type=ScopeType.ALL,
            basis='Управление доступами', granted_by=self.dual,
        )
        grant = PortalGrant.objects.create(
            person=target, account=self.account, starts=timezone.localdate(),
            basis='Исходный доступ', verified_by=self.dual, can_view_account=True,
        )
        manager_user.refresh_from_db()
        self.client.force_login(manager_user)
        response = self.client.post(
            f'/work/access/grants/{grant.pk}/',
            {
                'action': 'rights',
                'can_view_account': 'on',
                'can_view_finance': 'on',
                'can_submit_water': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        grant.refresh_from_db()
        self.assertTrue(grant.can_view_finance)
        self.assertTrue(grant.can_submit_water)
        self.assertGreaterEqual(grant.history.count(), 2)

        invalid = self.client.post(
            f'/work/access/grants/{grant.pk}/',
            {'action': 'rights', 'can_view_finance': 'on'},
        )
        self.assertEqual(invalid.status_code, 200)
        self.assertContains(invalid, 'Специальные права требуют базового доступа')
        grant.refresh_from_db()
        self.assertTrue(grant.can_view_account)

    def test_role_functions_can_be_toggled_only_inside_original_envelope(self):
        person, user = self._person_with_login('Настраиваемый Старший', 'toggle-senior-v2')
        assignment = assignment_from_role(
            person=person, role_code='line_senior', scope_type=ScopeType.WATER_GROUP,
            scope_object_id=self.group.pk, basis='Старший с настройкой', granted_by=self.dual,
        )
        self.client.force_login(self.dual)
        keep = ['accounts.view', 'water.view', 'water.meters.view', 'water.topology.view']
        response = self.client.post(
            f'/work/access/people/{person.pk}/',
            {
                'action': 'edit_assignment', 'assignment_id': assignment.pk,
                'capabilities': keep, 'reason': 'Оставлены только функции просмотра',
            },
        )
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(set(assignment.capabilities), set(keep))
        self.assertIn('water.observation.review_line', assignment.allowed_capabilities)
        self.assertFalse(can(user, 'water.observation.review_line', scope=ScopeRef(ScopeType.WATER_GROUP, self.group.pk)))
        self.assertTrue(can(user, 'water.view', scope=ScopeRef(ScopeType.WATER_GROUP, self.group.pk)))

        before = list(assignment.capabilities)
        invalid = self.client.post(
            f'/work/access/people/{person.pk}/',
            {
                'action': 'edit_assignment', 'assignment_id': assignment.pk,
                'capabilities': keep + ['finance.view'], 'reason': 'Попытка расширения',
            },
        )
        self.assertEqual(invalid.status_code, 200)
        assignment.refresh_from_db()
        self.assertEqual(assignment.capabilities, before)
        self.assertContains(invalid, 'пределами исходной роли')

    def test_access_admin_can_link_identity_but_cannot_issue_service_role(self):
        manager_person, manager_user = self._person_with_login('Менеджер Идентичности', 'identity-manager-v2')
        target = Person.objects.create(full_name='Человек без логина')
        target_user = User.objects.create_user(username='target-login-v2', email='target-login-v2@example.test')
        assignment_from_role(
            person=manager_person, role_code='access_admin', scope_type=ScopeType.ALL,
            basis='Управление доступами', granted_by=self.dual,
        )
        manager_user.refresh_from_db()
        self.client.force_login(manager_user)
        linked = self.client.post(
            f'/work/access/people/{target.pk}/',
            {
                'action': 'identity',
                'identity-user': target_user.pk,
                'identity-basis': 'Личность проверена менеджером доступа',
            },
        )
        self.assertEqual(linked.status_code, 302)
        self.assertEqual(target.resident_identity.user_id, target_user.pk)

        denied = self.client.post(
            f'/work/access/people/{target.pk}/',
            {
                'action': 'assign',
                'assign-role_code': 'controller',
                'assign-scope_type': 'water_group',
                'assign-water_group': self.group.pk,
                'assign-starts': timezone.localdate().isoformat(),
                'assign-basis': 'Недопустимая эскалация',
            },
        )
        self.assertEqual(denied.status_code, 403)
        self.assertFalse(AccessAssignment.objects.filter(person=target, role_code='controller').exists())

    def test_tsn_admin_can_issue_service_role(self):
        admin_person, admin_user = self._person_with_login('V2 Администратор ТСН', 'tsn-admin-v2')
        target, _ = self._person_with_login('Назначаемый Контролёр', 'assigned-controller-v2')
        assignment_from_role(
            person=admin_person, role_code='tsn_admin', scope_type=ScopeType.ALL,
            basis='Администратор ТСН', granted_by=self.dual,
        )
        admin_user.refresh_from_db()
        self.client.force_login(admin_user)
        response = self.client.post(
            f'/work/access/people/{target.pk}/',
            {
                'action': 'assign',
                'assign-role_code': 'controller',
                'assign-scope_type': 'water_group',
                'assign-water_group': self.group.pk,
                'assign-starts': timezone.localdate().isoformat(),
                'assign-basis': 'Назначение контролёром',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(AccessAssignment.objects.filter(person=target, role_code='controller').exists())


    def test_one_person_can_be_resident_line_senior_and_board_member(self):
        person, user = self._person_with_login('Три Роли', 'three-roles-v2')
        PortalGrant.objects.create(
            person=person, account=self.account, starts=timezone.localdate(),
            basis='Личный доступ', verified_by=self.dual,
            can_view_account=True, can_submit_water=True,
        )
        assignment_from_role(
            person=person, role_code='line_senior', scope_type=ScopeType.WATER_GROUP,
            scope_object_id=self.group.pk, basis='Старший линии', granted_by=self.dual,
        )
        membership = BoardMembership.objects.create(
            user=user, person=person, role=BoardMembership.ROLE_MEMBER,
            starts=timezone.localdate(), basis='Решение о составе правления',
        )
        now = timezone.now()
        poll = BoardPoll.objects.create(
            title='V2 совместный контекст', opens_at=now - timedelta(minutes=5),
            closes_at=now + timedelta(days=1), created_by=self.dual,
        )
        question = BoardQuestion.objects.create(poll=poll, order=1, text='Подтвердить тест?')
        user.refresh_from_db()
        self.client.force_login(user)

        self.assertEqual(self.client.get('/admin/cabinet/').status_code, 200)
        self.assertEqual(self.client.get('/admin/water/controller-workspace/').status_code, 200)
        board = self.client.get(f'/admin/cabinet/board/poll/{poll.pk}/')
        self.assertEqual(board.status_code, 200)
        vote = self.client.post(
            f'/admin/cabinet/board/poll/{poll.pk}/',
            {'action': 'vote', 'question_id': question.pk, 'choice': 'for', 'comment': 'Один логин'},
        )
        self.assertEqual(vote.status_code, 302)
        self.assertTrue(BoardVote.objects.filter(question=question, user=user).exists())
        self.assertEqual(membership.person, person)

    def test_v2_role_presets_open_their_real_work_modules_without_legacy_groups(self):
        cases = [
            ('finance_viewer', '/work/finance/'),
            ('appeals_operator', '/work/appeals/'),
            ('account_documents', '/work/documents/'),
            ('governance_secretary', '/work/governance/'),
            ('security_reviewer', '/work/security/'),
            ('private_registry', '/admin/water/person/'),
        ]
        for index, (role_code, path) in enumerate(cases, start=1):
            person, user = self._person_with_login(f'V2 модуль {index}', f'v2-module-{index}')
            assignment_from_role(
                person=person, role_code=role_code, scope_type=ScopeType.ALL,
                basis=f'Проверка модуля {role_code}', granted_by=self.dual,
            )
            user.refresh_from_db()
            self.assertTrue(user.is_staff)
            self.assertEqual(user.groups.count(), 0)
            self.client.force_login(user)
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, f'{role_code} -> {path}')

    def test_node_moderator_cannot_see_submission_from_other_node(self):
        person, user = self._person_with_login('Модератор Узла', 'node-moderator-v2')
        other_node = SupplyNode.objects.create(name='V2 чужой узел')
        own_meter = Meter.objects.create(serial='V2-NODE-OWN', kind='main', node=self.node)
        other_meter = Meter.objects.create(serial='V2-NODE-OTHER', kind='main', node=other_node)
        submitter = User.objects.create_user(username='v2-node-submitter')
        ControllerReadingSubmission.objects.create(
            meter=own_meter, date=timezone.localdate(), value='10.000', submitted_by=submitter,
        )
        ControllerReadingSubmission.objects.create(
            meter=other_meter, date=timezone.localdate(), value='20.000', submitted_by=submitter,
        )
        assignment_from_role(
            person=person, role_code='water_moderator', scope_type=ScopeType.SUPPLY_NODE,
            scope_object_id=self.node.pk, basis='Модератор своего узла', granted_by=self.dual,
        )
        user.refresh_from_db()
        self.client.force_login(user)
        response = self.client.get('/admin/water/controllerreadingsubmission/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '10,000')
        self.assertNotContains(response, '20,000')
        self.assertContains(response, '1 Наблюдение счётчика')

    def test_staff_resident_keeps_resident_navigation(self):
        person, user = self._person_with_login('Staff Житель', 'staff-resident-nav-v2')
        PortalGrant.objects.create(
            person=person, account=self.account, starts=timezone.localdate(),
            basis='Личный доступ', verified_by=self.dual, can_view_account=True,
            can_view_finance=True, can_submit_water=True, can_view_documents=True,
            can_use_appeals=True,
        )
        assignment_from_role(
            person=person, role_code='line_senior', scope_type=ScopeType.WATER_GROUP,
            scope_object_id=self.group.pk, basis='Совмещённая роль', granted_by=self.dual,
        )
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.client.force_login(user)
        response = self.client.get(f'/admin/cabinet/account/{self.account.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Мои участки')
        self.assertContains(response, 'Платежи')
        self.assertContains(response, 'Вода')
