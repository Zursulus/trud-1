from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from .models import Account, AppealCategory, Person, ResidentAppeal, User
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity


class HistoricalPortalPermissionTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.account = Account.objects.create(number='HIST-1', plot='Историческая 1')
        self.user = User.objects.create_user(username='historical-access@example.test')
        self.person = Person.objects.create(full_name='Тестовый Исторический Доступ')
        self.staff = User.objects.create_user(username='historical-verifier', is_staff=True)
        self.category = AppealCategory.objects.create(name='Исторический тест')
        ResidentIdentity.objects.create(
            user=self.user,
            person=self.person,
            verified_by=self.staff,
            basis='Синтетическая историческая проверка',
        )
        PortalGrant.objects.create(
            person=self.person,
            account=self.account,
            starts=self.today - timedelta(days=10),
            ends=self.today,
            can_view_account=True,
            can_use_appeals=True,
            basis='Синтетическое срочное полномочие',
            verified_by=self.staff,
        )

    def test_existing_appeal_uses_authority_at_opened_date_after_grant_expires(self):
        appeal = ResidentAppeal(
            account=self.account,
            author=self.user,
            category=self.category,
            subject='Обращение до окончания полномочия',
            message='Синтетическое сообщение',
            opened_at=timezone.now() - timedelta(days=1),
        )
        appeal.save()

        appeal.status = 'in_progress'
        appeal.save()
        self.assertEqual(appeal.history.count(), 2)

    def test_new_appeal_is_rejected_after_grant_expires(self):
        appeal = ResidentAppeal(
            account=self.account,
            author=self.user,
            category=self.category,
            subject='Обращение после окончания полномочия',
            message='Синтетическое сообщение',
        )
        with self.assertRaises(ValidationError):
            appeal.save()
