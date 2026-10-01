"""Accepted P1: ended explicit grants cannot revive broad legacy authority."""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from .access_control import create_delegation
from .models import Account, Person, ResidentAccess, User
from .portal_permissions import (
    CAP_APPEALS, CAP_DOCUMENTS, CAP_FINANCE, CAP_REPRESENT,
    CAP_SUBMIT_WATER, CAP_VIEW_ACCOUNT, PortalGrant,
    resolved_access_at, resolved_accesses,
)
from .resident_models import ResidentIdentity


class PortalGrantRevocationTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.staff = User.objects.create_user(username="revocation-verifier", is_staff=True)
        self.user = User.objects.create_user(username="revocation-resident")
        self.person = Person.objects.create(full_name="Synthetic revocation resident")
        ResidentIdentity.objects.create(
            user=self.user, person=self.person, verified_by=self.staff, basis="Synthetic identity",
        )
        self.account = Account.objects.create(number="REVOKE-A", plot="Synthetic A")
        self.other = Account.objects.create(number="REVOKE-B", plot="Synthetic B")
        for account in (self.account, self.other):
            ResidentAccess.objects.create(
                user=self.user, account=account, role="owner", starts=self.today - timedelta(days=60),
            )

    def _grant(self, **overrides):
        values = dict(
            person=self.person, account=self.account, starts=self.today - timedelta(days=10),
            can_view_account=True, can_use_appeals=True, verified_by=self.staff, basis="Synthetic grant",
        )
        values.update(overrides)
        return PortalGrant.objects.create(**values)

    def test_ended_grant_blocks_every_legacy_capability_only_on_its_account(self):
        self._grant(ends=self.today)
        self.assertEqual([row.account_id for row in resolved_accesses(self.user)], [self.other.pk])
        for capability in (CAP_VIEW_ACCOUNT, CAP_FINANCE, CAP_SUBMIT_WATER, CAP_DOCUMENTS, CAP_APPEALS, CAP_REPRESENT):
            with self.subTest(capability=capability):
                self.assertIsNone(resolved_access_at(self.user, self.account.pk, capability, self.today))
                self.assertEqual(resolved_access_at(self.user, self.other.pk, capability, self.today).source, "legacy")
        # A resolver decision must not rewrite or delete historical legacy rows.
        self.assertEqual(ResidentAccess.objects.filter(user=self.user).count(), 2)
        self.assertEqual(list(ResidentAccess.objects.filter(user=self.user).values_list("ends", flat=True)), [None, None])
        self.assertEqual(ResidentAccess.history.filter(user_id=self.user.pk).count(), 2)

    def test_history_gap_renewal_and_second_end_have_exact_date_boundaries(self):
        self._grant(ends=self.today)
        self._grant(starts=self.today + timedelta(days=10), ends=self.today + timedelta(days=20),
                    can_use_appeals=False, can_view_finance=True)
        for offset, source, finance, appeals in (
            (-11, "legacy", True, True), (-10, "grant", False, True), (-1, "grant", False, True),
            (0, None, False, False), (9, None, False, False),
            (10, "grant", True, False), (19, "grant", True, False),
            (20, None, False, False), (30, None, False, False),
        ):
            with self.subTest(offset=offset):
                on_date = self.today + timedelta(days=offset)
                access = resolved_access_at(self.user, self.account.pk, on_date=on_date)
                self.assertEqual(access.source if access else None, source)
                self.assertEqual(resolved_access_at(self.user, self.account.pk, CAP_FINANCE, on_date) is not None, finance)
                self.assertEqual(resolved_access_at(self.user, self.account.pk, CAP_APPEALS, on_date) is not None, appeals)
                self.assertEqual(resolved_access_at(self.user, self.other.pk, CAP_FINANCE, on_date).source, "legacy")

    def test_future_first_grant_preserves_legacy_until_its_start(self):
        self._grant(starts=self.today + timedelta(days=5), ends=self.today + timedelta(days=10))
        self.assertEqual(resolved_access_at(self.user, self.account.pk, CAP_FINANCE, self.today).source, "legacy")
        self.assertIsNone(resolved_access_at(self.user, self.account.pk, CAP_FINANCE, self.today + timedelta(days=5)))
        self.assertIsNone(resolved_access_at(self.user, self.account.pk, on_date=self.today + timedelta(days=10)))

    def test_another_persons_legacy_access_to_same_account_is_not_revoked(self):
        self._grant(ends=self.today)
        neighbour = User.objects.create_user(username="revocation-neighbour")
        person = Person.objects.create(full_name="Synthetic neighbour")
        ResidentIdentity.objects.create(user=neighbour, person=person, verified_by=self.staff, basis="Synthetic identity")
        ResidentAccess.objects.create(user=neighbour, account=self.account, role="owner", starts=self.today - timedelta(days=60))
        self.assertIsNone(resolved_access_at(self.user, self.account.pk))
        self.assertEqual(resolved_access_at(neighbour, self.account.pk, CAP_FINANCE).source, "legacy")

    def test_independent_verified_delegation_does_not_restore_legacy_capabilities(self):
        self._grant(ends=self.today)
        owner = Person.objects.create(full_name="Synthetic delegator")
        self._grant(person=owner, can_view_finance=True)
        create_delegation(
            delegator=owner, delegate=self.person, account_id=self.account.pk,
            capabilities=["resident.account.view", "resident.finance.view"],
            starts=self.today - timedelta(days=5), ends=self.today + timedelta(days=5),
            basis="Synthetic delegation", verified_by=self.staff,
        )
        self.assertEqual(resolved_access_at(self.user, self.account.pk, CAP_FINANCE).source, "delegation")
        self.assertIsNone(resolved_access_at(self.user, self.account.pk, CAP_APPEALS))
        self.assertIsNone(resolved_access_at(self.user, self.account.pk, CAP_DOCUMENTS))
        self.assertIsNone(resolved_access_at(self.user, self.account.pk, on_date=self.today + timedelta(days=5)))

    def test_outgoing_delegation_loses_authority_when_its_source_grant_ends(self):
        grant = self._grant(can_view_finance=True)
        delegate = Person.objects.create(full_name="Synthetic delegate")
        user = User.objects.create_user(username="revocation-delegate")
        ResidentIdentity.objects.create(user=user, person=delegate, verified_by=self.staff, basis="Synthetic identity")
        create_delegation(
            delegator=self.person, delegate=delegate, account_id=self.account.pk,
            capabilities=["resident.account.view", "resident.finance.view"],
            starts=self.today - timedelta(days=5), basis="Synthetic delegation", verified_by=self.staff,
        )
        grant.ends = self.today
        grant._history_user = self.staff
        grant._change_reason = "End synthetic source grant"
        grant.save()
        self.assertEqual(resolved_access_at(user, self.account.pk, CAP_FINANCE, self.today - timedelta(days=1)).source, "delegation")
        self.assertIsNone(resolved_access_at(user, self.account.pk, CAP_FINANCE, self.today))
