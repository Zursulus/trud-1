from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from water.models import Account, ResidentAccess, User
from water.resident_numbers import ResidentNumberSlot


class ResidentNumberReservationTests(TestCase):
    def test_slots_1_through_310_and_333_are_reserved(self):
        self.assertEqual(
            ResidentNumberSlot.objects.filter(number__gte=1, number__lte=310, purpose='resident').count(),
            310,
        )
        test_slot = ResidentNumberSlot.objects.get(pk=333)
        self.assertEqual(test_slot.purpose, 'test')
        self.assertIsNone(test_slot.user_id)

    def test_test_slot_rejects_staff_user(self):
        user = User.objects.create_user(username='staff-test', password='irrelevant-password', is_staff=True)
        slot = ResidentNumberSlot.objects.get(pk=333)
        slot.user = user
        with self.assertRaisesMessage(Exception, 'Номер жителя нельзя назначать сотруднику'):
            slot.save()

    def test_provision_command_requires_synthetic_account(self):
        with self.assertRaises(CommandError):
            call_command('provision_resident_test', stdout=StringIO())

        real_account = Account.objects.create(number='REAL-1', plot='Реальный участок')
        with self.assertRaisesMessage(CommandError, 'только к синтетическому счёту TEST-333'):
            call_command(
                'provision_resident_test', account_id=real_account.pk, stdout=StringIO(),
            )

    def test_provision_command_creates_synthetic_account_number_333_login_and_payer_access(self):
        output = StringIO()
        call_command(
            'provision_resident_test',
            ensure_synthetic_account=True,
            username='test333',
            stdout=output,
        )

        account = Account.objects.get(number='TEST-333')
        self.assertEqual(account.plot, 'Тестовый участок №333')
        self.assertEqual(account.contact_name, '')
        self.assertEqual(account.phone, '')

        slot = ResidentNumberSlot.objects.select_related('user').get(pk=333)
        self.assertIsNotNone(slot.user_id)
        self.assertEqual(slot.user.username, 'test333')
        self.assertEqual(slot.user.first_name, '')
        self.assertEqual(slot.user.last_name, '')
        self.assertEqual(slot.user.email, '')
        self.assertFalse(slot.user.is_staff)
        self.assertFalse(slot.user.is_superuser)
        self.assertTrue(ResidentAccess.objects.filter(
            user=slot.user,
            account=account,
            role='payer',
            ends__isnull=True,
        ).exists())

        password_line = next(
            line for line in output.getvalue().splitlines()
            if line.startswith('Временный пароль: ')
        )
        temporary_password = password_line.split(': ', 1)[1]
        self.assertTrue(slot.user.check_password(temporary_password))

        second_output = StringIO()
        call_command(
            'provision_resident_test',
            ensure_synthetic_account=True,
            username='ignored-after-create',
            stdout=second_output,
        )
        self.assertEqual(User.objects.filter(username='test333').count(), 1)
        self.assertEqual(ResidentAccess.objects.filter(user=slot.user, account=account).count(), 1)
        self.assertIn('Пароль не менялся', second_output.getvalue())
