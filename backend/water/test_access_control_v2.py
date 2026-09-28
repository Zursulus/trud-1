from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase

from .access_control import assignment_from_role, create_delegation
from .access_policy import ScopeType
from .access_resolver import explain
from .access_scope import ScopeRef
from .models import Account, Membership, Person, SupplyNode, User, WaterGroup
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity


class AccessControlV2Tests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='rights-admin', email='admin@example.test', is_staff=True,
        )
        self.node = SupplyNode.objects.create(name='Общий узел')
        self.line = WaterGroup.objects.create(name='Миндальная', node=self.node)
        self.account = Account.objects.create(number='A-1', plot='Горная 2')
        Membership.objects.create(
            account=self.account, group=self.line, starts=date(2026, 1, 1),
        )

    def _person_user(self, slug, *, staff=False):
        person = Person.objects.create(full_name=f'Человек {slug}')
        user = User.objects.create_user(
            username=slug, email=f'{slug}@example.test', is_staff=staff,
        )
        ResidentIdentity.objects.create(
            user=user, person=person, verified_by=self.admin, basis='Синтетический тест',
        )
        return person, user

    def test_staff_login_can_be_resident_and_line_senior_at_once(self):
        person, user = self._person_user('senior-resident', staff=True)
        PortalGrant.objects.create(
            person=person, account=self.account, starts=date(2026, 1, 1),
            can_view_account=True, can_submit_water=True,
            basis='Личный доступ', verified_by=self.admin,
        )
        assignment_from_role(
            person=person, role_code='line_senior',
            scope_type=ScopeType.WATER_GROUP, scope_object_id=self.line.pk,
            starts=date(2026, 1, 1), basis='Назначен старшим', granted_by=self.admin,
        )
        personal = explain(
            user, 'resident.water.submit',
            scope=ScopeRef(ScopeType.ACCOUNT, self.account.pk), on_date=date(2026, 5, 1),
        )
        service = explain(
            user, 'water.observation.review_line',
            scope=ScopeRef(ScopeType.ACCOUNT, self.account.pk), on_date=date(2026, 5, 1),
        )
        self.assertTrue(personal.allowed)
        self.assertEqual(personal.source, 'portal_grant')
        self.assertTrue(service.allowed)
        self.assertEqual(service.source, 'v2_assignment')

    def test_line_senior_and_controller_can_coexist_on_same_line(self):
        senior_person, senior_user = self._person_user('senior')
        controller_person, controller_user = self._person_user('controller')
        assignment_from_role(
            person=senior_person, role_code='line_senior',
            scope_type=ScopeType.WATER_GROUP, scope_object_id=self.line.pk,
            basis='Старший', granted_by=self.admin,
        )
        assignment_from_role(
            person=controller_person, role_code='controller',
            scope_type=ScopeType.WATER_GROUP, scope_object_id=self.line.pk,
            basis='Контролёр', granted_by=self.admin,
        )
        senior_review = explain(
            senior_user, 'water.observation.review_line',
            scope=ScopeRef(ScopeType.ACCOUNT, self.account.pk),
        )
        controller_observe = explain(
            controller_user, 'water.observation.submit',
            scope=ScopeRef(ScopeType.WATER_GROUP, self.line.pk),
        )
        self.assertTrue(senior_review.allowed)
        self.assertTrue(controller_observe.allowed)
        self.assertFalse(explain(
            controller_user, 'water.observation.review_line',
            scope=ScopeRef(ScopeType.WATER_GROUP, self.line.pk),
        ).allowed)
        self.assertFalse(explain(
            senior_user, 'water.observation.submit',
            scope=ScopeRef(ScopeType.WATER_GROUP, self.line.pk),
        ).allowed)

    def test_line_assignment_follows_house_membership_by_date(self):
        person, user = self._person_user('dated-senior')
        assignment_from_role(
            person=person, role_code='line_senior',
            scope_type=ScopeType.WATER_GROUP, scope_object_id=self.line.pk,
            starts=date(2026, 1, 1), basis='Старший', granted_by=self.admin,
        )
        membership = Membership.objects.get(account=self.account, group=self.line)
        membership.ends = date(2026, 7, 1)
        membership.save()
        scope = ScopeRef(ScopeType.ACCOUNT, self.account.pk)
        self.assertTrue(explain(
            user, 'water.observation.review_line', scope=scope, on_date=date(2026, 6, 30),
        ).allowed)
        self.assertFalse(explain(
            user, 'water.observation.review_line', scope=scope, on_date=date(2026, 7, 1),
        ).allowed)

    def test_delegation_transfers_only_selected_personal_right(self):
        owner, owner_user = self._person_user('owner')
        delegate, delegate_user = self._person_user('delegate')
        PortalGrant.objects.create(
            person=owner, account=self.account, starts=date(2026, 1, 1),
            can_view_account=True, can_view_finance=True, can_submit_water=True,
            basis='Права владельца', verified_by=self.admin,
        )
        create_delegation(
            delegator=owner, delegate=delegate,
            capabilities=['resident.account.view', 'resident.water.submit'], account_id=self.account.pk,
            starts=date(2026, 2, 1), ends=date(2026, 12, 1),
            basis='Передача показаний', verified_by=self.admin,
        )
        scope = ScopeRef(ScopeType.ACCOUNT, self.account.pk)
        water = explain(
            delegate_user, 'resident.water.submit', scope=scope, on_date=date(2026, 5, 1),
        )
        finance = explain(
            delegate_user, 'resident.finance.view', scope=scope, on_date=date(2026, 5, 1),
        )
        self.assertTrue(water.allowed)
        self.assertEqual(water.source, 'v2_delegation')
        self.assertFalse(finance.allowed)
        self.assertTrue(explain(
            owner_user, 'resident.finance.view', scope=scope, on_date=date(2026, 5, 1),
        ).allowed)

    def test_non_delegable_service_right_is_rejected(self):
        owner, _ = self._person_user('service-owner')
        delegate, _ = self._person_user('service-delegate')
        with self.assertRaises(ValidationError):
            create_delegation(
                delegator=owner, delegate=delegate,
                capabilities=['finance.payment.confirm'], account_id=self.account.pk,
                basis='Нельзя', verified_by=self.admin,
            )

    def test_delegate_cannot_redelegate_delegated_authority(self):
        owner, _ = self._person_user('owner-chain')
        delegate, _ = self._person_user('delegate-chain')
        third, _ = self._person_user('third-chain')
        PortalGrant.objects.create(
            person=owner, account=self.account, starts=date(2026, 1, 1),
            can_view_account=True, can_submit_water=True,
            basis='Прямое право', verified_by=self.admin,
        )
        create_delegation(
            delegator=owner, delegate=delegate,
            capabilities=['resident.account.view', 'resident.water.submit'], account_id=self.account.pk,
            starts=date(2026, 2, 1), basis='Первое', verified_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            create_delegation(
                delegator=delegate, delegate=third,
                capabilities=['resident.account.view', 'resident.water.submit'], account_id=self.account.pk,
                starts=date(2026, 3, 1), basis='Переделегирование', verified_by=self.admin,
            )

    def test_delegation_cannot_outlive_source_authority(self):
        owner, _ = self._person_user('short-owner')
        delegate, _ = self._person_user('short-delegate')
        PortalGrant.objects.create(
            person=owner, account=self.account, starts=date(2026, 1, 1), ends=date(2026, 6, 1),
            can_view_account=True, can_submit_water=True,
            basis='До июня', verified_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            create_delegation(
                delegator=owner, delegate=delegate,
                capabilities=['resident.account.view', 'resident.water.submit'], account_id=self.account.pk,
                starts=date(2026, 2, 1), ends=date(2026, 7, 1),
                basis='Слишком долго', verified_by=self.admin,
            )

    def test_ended_assignment_stops_without_rewriting_history(self):
        person, user = self._person_user('ended')
        assignment = assignment_from_role(
            person=person, role_code='line_senior',
            scope_type=ScopeType.WATER_GROUP, scope_object_id=self.line.pk,
            starts=date(2026, 1, 1), ends=date(2026, 6, 1),
            basis='Временно', granted_by=self.admin,
        )
        scope = ScopeRef(ScopeType.WATER_GROUP, self.line.pk)
        self.assertTrue(explain(user, 'water.balance.view', scope=scope, on_date=date(2026, 5, 31)).allowed)
        self.assertFalse(explain(user, 'water.balance.view', scope=scope, on_date=date(2026, 6, 1)).allowed)
        self.assertTrue(assignment.history.exists())

    def test_service_assignment_manages_staff_gateway_without_becoming_authority_source(self):
        from .access_control import revoke_assignment

        person, user = self._person_user('gateway')
        self.assertFalse(user.is_staff)
        assignment = assignment_from_role(
            person=person, role_code='line_senior',
            scope_type=ScopeType.WATER_GROUP, scope_object_id=self.line.pk,
            basis='Назначение', granted_by=self.admin,
        )
        user.refresh_from_db()
        user.resident_identity.refresh_from_db()
        self.assertTrue(user.is_staff)
        self.assertTrue(user.resident_identity.v2_staff_gateway_managed)
        self.assertTrue(explain(
            user, 'water.balance.view', scope=ScopeRef(ScopeType.WATER_GROUP, self.line.pk),
        ).allowed)

        revoke_assignment(assignment, actor=self.admin, reason='Смена старшего')
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertFalse(explain(
            user, 'water.balance.view', scope=ScopeRef(ScopeType.WATER_GROUP, self.line.pk),
        ).allowed)

    def test_preexisting_staff_flag_is_not_removed_when_v2_assignment_ends(self):
        from .access_control import revoke_assignment

        person, user = self._person_user('legacy-staff', staff=True)
        assignment = assignment_from_role(
            person=person, role_code='line_senior',
            scope_type=ScopeType.WATER_GROUP, scope_object_id=self.line.pk,
            basis='Назначение', granted_by=self.admin,
        )
        user.resident_identity.refresh_from_db()
        self.assertFalse(user.resident_identity.v2_staff_gateway_managed)
        revoke_assignment(assignment, actor=self.admin, reason='Завершено')
        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_replacing_login_keeps_person_authority_and_moves_gateway(self):
        from .access_control import transfer_identity

        person, old_user = self._person_user('old-login')
        assignment_from_role(
            person=person, role_code='line_senior',
            scope_type=ScopeType.WATER_GROUP, scope_object_id=self.line.pk,
            basis='Старший', granted_by=self.admin,
        )
        identity = old_user.resident_identity
        new_user = User.objects.create_user(username='new-login', email='new-login@example.test')
        transfer_identity(
            identity=identity, new_user=new_user, actor=self.admin,
            basis='Замена логина по подтверждённому обращению',
        )
        old_user.refresh_from_db()
        new_user.refresh_from_db()
        self.assertFalse(old_user.is_staff)
        self.assertTrue(new_user.is_staff)
        scope = ScopeRef(ScopeType.WATER_GROUP, self.line.pk)
        self.assertFalse(explain(old_user, 'water.balance.view', scope=scope).allowed)
        self.assertTrue(explain(new_user, 'water.balance.view', scope=scope).allowed)

    def test_board_membership_is_linked_to_person_when_identity_exists(self):
        from .board_polls import BoardMembership, active_board_membership

        person, user = self._person_user('board-linked')
        membership = BoardMembership.objects.create(
            user=user, role='member', starts=date(2026, 1, 1),
        )
        membership.refresh_from_db()
        self.assertEqual(membership.person_id, person.pk)
        self.assertEqual(active_board_membership(user, date(2026, 5, 1)).pk, membership.pk)
