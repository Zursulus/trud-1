from io import StringIO
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .models import Account, LandPlot, Person, PlotRelation, ResidentAccess, User
from .portal_permissions import PortalGrant
from .private_registry import MemberRegistryEntry
from .registry_migration_plan import analyze_member_registry
from .resident_models import ResidentIdentity, TsnMembership
from .resident_numbers import ResidentNumberSlot, TEST_RESIDENT_NUMBER


class RegistryMigrationPlanTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.staff = User.objects.create_user(username='registry-plan-verifier', is_staff=True)

    def _entry(self, number, *, person=None, account=None, user=None, phone='', email=''):
        slot = ResidentNumberSlot.objects.create(
            number=number,
            purpose=ResidentNumberSlot.PURPOSE_RESIDENT,
            user=user,
        )
        return MemberRegistryEntry.objects.create(
            resident_number=slot,
            person=person,
            account=account,
            phone=phone,
            email=email,
        )

    def test_explicit_person_account_and_unique_plot_create_only_candidate_scope(self):
        user = User.objects.create_user(username='registry-candidate')
        person = Person.objects.create(full_name='Синтетический Кандидат')
        account = Account.objects.create(number='REG-CAND-1', plot='Тестовая 1')
        LandPlot.objects.create(label='REG-PLOT-1', account=account)
        self._entry(1, person=person, account=account, user=user)

        plan = analyze_member_registry(on_date=self.today)

        self.assertEqual(plan.ids('identity_candidate'), [1])
        self.assertEqual(plan.ids('plot_relation_candidate'), [1])
        self.assertEqual(plan.ids('portal_grant_candidate'), [1])
        self.assertEqual(plan.ids('manual_review'), [])
        self.assertEqual(TsnMembership.objects.count(), 0)
        self.assertEqual(ResidentIdentity.objects.count(), 0)
        self.assertEqual(PlotRelation.objects.count(), 0)
        self.assertEqual(PortalGrant.objects.count(), 0)

    def test_shared_contact_is_manual_review_and_never_identity_evidence(self):
        for number in (1, 2):
            user = User.objects.create_user(username=f'registry-shared-{number}')
            person = Person.objects.create(full_name=f'Синтетический Общий Контакт {number}')
            account = Account.objects.create(number=f'REG-SHARED-{number}', plot=f'Тестовая {number}')
            LandPlot.objects.create(label=f'REG-SHARED-PLOT-{number}', account=account)
            self._entry(
                number,
                person=person,
                account=account,
                user=user,
                phone='+7 (999) 111-22-33',
                email='shared@example.test',
            )

        plan = analyze_member_registry(on_date=self.today)

        self.assertEqual(plan.ids('shared_contact'), [1, 2])
        self.assertEqual(plan.ids('manual_review'), [1, 2])
        self.assertEqual(plan.ids('identity_candidate'), [])
        self.assertEqual(plan.ids('plot_relation_candidate'), [])
        self.assertEqual(plan.ids('portal_grant_candidate'), [])

    def test_existing_identity_relation_grant_and_legacy_are_not_proposed_again(self):
        user = User.objects.create_user(username='registry-existing')
        person = Person.objects.create(full_name='Синтетический Существующий')
        account = Account.objects.create(number='REG-EXIST-1', plot='Тестовая 10')
        plot = LandPlot.objects.create(label='REG-EXIST-PLOT', account=account)
        self._entry(10, person=person, account=account, user=user)
        ResidentIdentity.objects.create(
            user=user,
            person=person,
            verified_by=self.staff,
            basis='Синтетическая проверка ZUR-71',
        )
        PlotRelation.objects.create(
            person=person,
            plot=plot,
            role=PlotRelation.OWNER,
            starts=self.today - timedelta(days=5),
            document='Синтетический документ',
        )
        PortalGrant.objects.create(
            person=person,
            account=account,
            starts=self.today - timedelta(days=5),
            basis='Синтетическое основание',
            verified_by=self.staff,
        )
        ResidentAccess.objects.create(
            user=user,
            account=account,
            role='owner',
            starts=self.today - timedelta(days=5),
        )

        plan = analyze_member_registry(on_date=self.today)

        self.assertEqual(plan.ids('identity_existing'), [10])
        self.assertEqual(plan.ids('plot_relation_existing'), [10])
        self.assertEqual(plan.ids('portal_grant_existing'), [10])
        self.assertEqual(plan.ids('legacy_access_present'), [10])
        self.assertEqual(plan.ids('identity_candidate'), [])
        self.assertEqual(plan.ids('plot_relation_candidate'), [])
        self.assertEqual(plan.ids('portal_grant_candidate'), [])

    def test_multiple_plots_are_ambiguous_and_never_get_relation_candidate(self):
        person = Person.objects.create(full_name='Синтетический Совладелец')
        account = Account.objects.create(number='REG-MULTI-1', plot='Тестовая 20')
        LandPlot.objects.create(label='REG-MULTI-A', account=account)
        LandPlot.objects.create(label='REG-MULTI-B', account=account)
        self._entry(20, person=person, account=account)

        plan = analyze_member_registry(on_date=self.today)

        self.assertEqual(plan.ids('plot_ambiguous'), [20])
        self.assertEqual(plan.ids('plot_relation_candidate'), [])
        self.assertEqual(plan.ids('manual_review'), [20])

    def test_registry_without_person_never_invents_one(self):
        account = Account.objects.create(number='REG-NOPERSON-1', plot='Тестовая 30')
        LandPlot.objects.create(label='REG-NOPERSON-PLOT', account=account)
        self._entry(
            30,
            account=account,
            phone='+7 900 000 00 30',
            email='unknown30@example.test',
        )
        before_people = Person.objects.count()

        plan = analyze_member_registry(on_date=self.today)

        self.assertEqual(plan.ids('person_manual_required'), [30])
        self.assertEqual(plan.ids('portal_grant_candidate'), [])
        self.assertEqual(Person.objects.count(), before_people)

    def test_test_number_333_is_excluded_even_if_invalid_row_bypasses_validation(self):
        test_user = User.objects.create_user(username='registry-test-333')
        slot = ResidentNumberSlot.objects.create(
            number=TEST_RESIDENT_NUMBER,
            purpose=ResidentNumberSlot.PURPOSE_TEST,
            user=test_user,
        )
        MemberRegistryEntry.objects.bulk_create([
            MemberRegistryEntry(
                resident_number=slot,
                phone='+7 900 333 33 33',
                email='test333@example.test',
            )
        ])

        plan = analyze_member_registry(on_date=self.today)

        self.assertEqual(plan.ids('excluded_nonresident'), [TEST_RESIDENT_NUMBER])
        self.assertEqual(plan.ids('eligible'), [])
        for category, ids in plan.categories.items():
            if category != 'excluded_nonresident':
                self.assertNotIn(TEST_RESIDENT_NUMBER, ids)

    def test_command_is_read_only_and_never_prints_pii(self):
        user = User.objects.create_user(username='registry-output-user')
        person = Person.objects.create(full_name='Секретное Имя Теста')
        account = Account.objects.create(number='REG-OUTPUT-1', plot='Секретный адрес')
        LandPlot.objects.create(label='REG-OUTPUT-PLOT', account=account)
        self._entry(
            40,
            person=person,
            account=account,
            user=user,
            phone='+7 900 123 45 67',
            email='private40@example.test',
        )
        before = {
            'entries': MemberRegistryEntry.objects.count(),
            'people': Person.objects.count(),
            'identities': ResidentIdentity.objects.count(),
            'relations': PlotRelation.objects.count(),
            'grants': PortalGrant.objects.count(),
            'memberships': TsnMembership.objects.count(),
        }
        output = StringIO()

        call_command('analyze_member_registry_migration', '--show-ids', stdout=output)

        after = {
            'entries': MemberRegistryEntry.objects.count(),
            'people': Person.objects.count(),
            'identities': ResidentIdentity.objects.count(),
            'relations': PlotRelation.objects.count(),
            'grants': PortalGrant.objects.count(),
            'memberships': TsnMembership.objects.count(),
        }
        text = output.getvalue()
        self.assertEqual(before, after)
        self.assertIn('№ 40', text)
        self.assertIn('Изменений в БД: 0', text)
        self.assertNotIn('Секретное Имя Теста', text)
        self.assertNotIn('Секретный адрес', text)
        self.assertNotIn('+7 900 123 45 67', text)
        self.assertNotIn('private40@example.test', text)
