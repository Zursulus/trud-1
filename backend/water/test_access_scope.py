from datetime import date

from django.test import TestCase

from .access_policy import ScopeType
from .access_scope import ScopeRef, scope_covers_account, scope_covers_meter, scoped_accounts
from .models import Account, Membership, Meter, SupplyNode, WaterGroup


class AccessScopeTests(TestCase):
    def setUp(self):
        self.node_a = SupplyNode.objects.create(name="Узел A")
        self.node_b = SupplyNode.objects.create(name="Узел B")
        self.group_a = WaterGroup.objects.create(name="Линия A", node=self.node_a)
        self.group_b = WaterGroup.objects.create(name="Линия B", node=self.node_b)
        self.account = Account.objects.create(number="A-1", plot="Дом 1")
        self.other = Account.objects.create(number="B-1", plot="Дом 2")
        Membership.objects.create(
            account=self.account, group=self.group_a,
            starts=date(2026, 1, 1), ends=date(2026, 7, 1),
        )
        Membership.objects.create(
            account=self.account, group=self.group_b,
            starts=date(2026, 7, 1),
        )
        Membership.objects.create(
            account=self.other, group=self.group_a,
            starts=date(2026, 1, 1),
        )
        self.individual = Meter.objects.create(
            serial="I-1", kind="individual", node=self.node_a,
            account=self.other, group=None,
        )
        self.line_meter = Meter.objects.create(
            serial="L-1", kind="line", node=self.node_a,
            group=self.group_a,
        )

    def test_account_moves_between_line_scopes_by_effective_date(self):
        a = ScopeRef(ScopeType.WATER_GROUP, self.group_a.pk)
        b = ScopeRef(ScopeType.WATER_GROUP, self.group_b.pk)
        self.assertTrue(scope_covers_account(a, self.account.pk, date(2026, 6, 30)))
        self.assertFalse(scope_covers_account(b, self.account.pk, date(2026, 6, 30)))
        self.assertFalse(scope_covers_account(a, self.account.pk, date(2026, 7, 1)))
        self.assertTrue(scope_covers_account(b, self.account.pk, date(2026, 7, 1)))

    def test_line_scope_derives_current_accounts_not_copied_list(self):
        scope = ScopeRef(ScopeType.WATER_GROUP, self.group_a.pk)
        before = set(scoped_accounts(scope, date(2026, 6, 30)).values_list("pk", flat=True))
        after = set(scoped_accounts(scope, date(2026, 7, 1)).values_list("pk", flat=True))
        self.assertEqual(before, {self.account.pk, self.other.pk})
        self.assertEqual(after, {self.other.pk})

    def test_supply_node_scope_inherits_through_active_group_membership(self):
        node_a = ScopeRef(ScopeType.SUPPLY_NODE, self.node_a.pk)
        node_b = ScopeRef(ScopeType.SUPPLY_NODE, self.node_b.pk)
        self.assertTrue(scope_covers_account(node_a, self.account.pk, date(2026, 6, 30)))
        self.assertFalse(scope_covers_account(node_b, self.account.pk, date(2026, 6, 30)))
        self.assertFalse(scope_covers_account(node_a, self.account.pk, date(2026, 7, 1)))
        self.assertTrue(scope_covers_account(node_b, self.account.pk, date(2026, 7, 1)))

    def test_line_scope_covers_line_meter_and_current_member_individual_meter(self):
        scope = ScopeRef(ScopeType.WATER_GROUP, self.group_a.pk)
        self.assertTrue(scope_covers_meter(scope, self.line_meter.pk, date(2026, 8, 1)))
        self.assertTrue(scope_covers_meter(scope, self.individual.pk, date(2026, 8, 1)))

    def test_line_scope_does_not_cover_unrelated_account_or_node(self):
        scope = ScopeRef(ScopeType.WATER_GROUP, self.group_b.pk)
        self.assertFalse(scope_covers_account(scope, self.other.pk, date(2026, 8, 1)))
        self.assertFalse(scope_covers_meter(scope, self.line_meter.pk, date(2026, 8, 1)))

    def test_archived_account_is_not_inherited(self):
        self.other.archived = True
        self.other.save()
        scope = ScopeRef(ScopeType.WATER_GROUP, self.group_a.pk)
        self.assertFalse(scope_covers_account(scope, self.other.pk, date(2026, 8, 1)))
        self.assertNotIn(self.other.pk, scoped_accounts(scope, date(2026, 8, 1)).values_list("pk", flat=True))

    def test_scope_ref_validates_object_id_shape(self):
        with self.assertRaises(ValueError):
            ScopeRef(ScopeType.WATER_GROUP)
        with self.assertRaises(ValueError):
            ScopeRef(ScopeType.ALL, 1)
