from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Account, ControllerReadingSubmission, Meter, Reading, ResidentAccess,
    SupplyNode, User,
)


class ResidentWaterFeedbackTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.account = Account.objects.create(number='FEEDBACK-1', plot='Тестовый участок')
        self.user = User.objects.create_user(username='water-feedback@example.test')
        ResidentAccess.objects.create(
            user=self.user, account=self.account, role='owner',
            starts=self.today - timedelta(days=30),
        )
        self.node = SupplyNode.objects.create(name='Тестовый узел обратной связи')
        self.meter = Meter.objects.create(
            serial='FEEDBACK-METER-1', kind='individual', node=self.node,
            account=self.account, commissioned_on=self.today - timedelta(days=30),
        )
        self.water_url = reverse('resident_water', args=[self.account.pk])
        self.submit_url = reverse('resident_reading', args=[self.account.pk, self.meter.pk])
        self.client.force_login(self.user)

    def submit(self, value='12.345', **overrides):
        data = {'date': self.today.isoformat(), 'value': value, 'notes': 'Тестовое показание'}
        data.update(overrides)
        return self.client.post(self.submit_url, data)

    def test_submission_returns_to_water_with_receipt_without_claiming_acceptance(self):
        accepted = Reading.objects.create(
            meter=self.meter, date=self.today - timedelta(days=1), value=Decimal('10.000'),
        )
        response = self.submit()
        self.assertRedirects(response, self.water_url, fetch_redirect_response=False)

        water = self.client.get(self.water_url)
        self.assertContains(water, 'Показание счётчика «FEEDBACK-METER-1» передано на проверку.')
        self.assertContains(water, 'Ваши последние заявки')
        self.assertContains(water, 'На проверке')
        self.assertContains(water, '12,345 м³')
        self.assertEqual(water.context['meter_rows'][0]['latest'], accepted)
        self.assertEqual(Reading.objects.filter(meter=self.meter).count(), 1)
        submission = ControllerReadingSubmission.objects.get(meter=self.meter)
        self.assertEqual(submission.status, 'pending')
        self.assertEqual(submission.history.first().history_user_id, self.user.pk)

    def test_repeat_updates_own_pending_observation_and_keeps_invalid_retry_unchanged(self):
        self.submit()
        submission = ControllerReadingSubmission.objects.get(meter=self.meter)
        self.assertRedirects(self.submit('13.456'), self.water_url)
        submission.refresh_from_db()
        self.assertEqual(submission.value, Decimal('13.456'))
        self.assertEqual(ControllerReadingSubmission.objects.filter(meter=self.meter).count(), 1)
        self.assertEqual(submission.history.count(), 2)

        invalid = self.submit('not-a-number', notes='Сохранить введённое примечание')
        self.assertEqual(invalid.status_code, 400)
        self.assertContains(invalid, 'Показание не отправлено.', status_code=400)
        self.assertContains(invalid, 'Сохранить введённое примечание', status_code=400)
        self.assertContains(invalid, f'value="{self.today.isoformat()}"', status_code=400)
        self.assertContains(invalid, 'Отменить и вернуться к воде', status_code=400)
        self.assertContains(invalid, f'class="active" href="{self.water_url}"', status_code=400)
        submission.refresh_from_db()
        self.assertEqual(submission.value, Decimal('13.456'))
        self.assertEqual(submission.history.count(), 2)
        self.assertFalse(Reading.objects.exists())

    def test_multiple_meter_statuses_show_only_current_residents_observations(self):
        second_meter = Meter.objects.create(
            serial='FEEDBACK-METER-2', kind='individual', node=self.node, account=self.account,
        )
        other_user = User.objects.create_user(username='other-water-feedback@example.test')
        records = [
            (self.meter, self.user, 'resident', 'pending', '12.345', ''),
            (second_meter, self.user, 'resident', 'rejected', '321.123', 'Internal moderation note'),
            (self.meter, other_user, 'resident', 'pending', '555.901', 'Another resident note'),
            (second_meter, self.user, 'controller', 'pending', '666.902', 'Staff observation note'),
        ]
        for meter, user, source, status, value, comment in records:
            ControllerReadingSubmission.objects.create(
                meter=meter, date=self.today, value=Decimal(value), submitted_by=user,
                source=source, status=status, review_comment=comment,
            )

        water = self.client.get(self.water_url)
        self.assertContains(water, 'Счётчик FEEDBACK-METER-1')
        self.assertContains(water, 'Счётчик FEEDBACK-METER-2')
        self.assertContains(water, 'На проверке')
        self.assertContains(water, 'Отклонено')
        self.assertContains(water, '12,345 м³')
        self.assertContains(water, '321,123 м³')
        for hidden in ('555,901', '666,902', 'Internal moderation note', 'Another resident note', 'Staff observation note'):
            self.assertNotContains(water, hidden)

    def test_other_account_and_retired_meter_keep_server_side_denial(self):
        other_account = Account.objects.create(number='FEEDBACK-OTHER')
        other_meter = Meter.objects.create(
            serial='FEEDBACK-OTHER-METER', kind='individual', node=self.node, account=other_account,
        )
        for account_id in (self.account.pk, other_account.pk):
            response = self.client.post(
                reverse('resident_reading', args=[account_id, other_meter.pk]),
                {'date': self.today.isoformat(), 'value': '20.000'},
            )
            self.assertEqual(response.status_code, 404)
        self.meter.retired_on = self.today - timedelta(days=1)
        self.meter.save()
        response = self.submit()
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Дата позже снятия счётчика с учёта.', status_code=400)
        self.assertFalse(ControllerReadingSubmission.objects.exists())

    def test_consumption_describes_actual_interval_instead_of_calendar_month(self):
        first_date = self.today - timedelta(days=9)
        Reading.objects.create(meter=self.meter, date=first_date, value=Decimal('10.000'))
        Reading.objects.create(meter=self.meter, date=self.today, value=Decimal('15.500'))
        water = self.client.get(self.water_url)
        self.assertContains(water, 'Расход между показаниями')
        self.assertContains(water, f'{first_date:%d.%m.%Y} — {self.today:%d.%m.%Y}')
        self.assertContains(water, '5,500 м³')
        self.assertNotContains(water, 'Расход за месяц')
