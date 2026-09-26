from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.utils import timezone

from ._legacy_suite import MFAAccessMixin
from .controller_scope import ControllerLineAccess
from .models import (
    Account, ControllerReadingSubmission, Membership, Meter, Reading,
    ResidentAccess, SupplyNode, User, WaterGroup,
)


class ObservationWorkflowTests(MFAAccessMixin, TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.node = SupplyNode.objects.create(name='Synthetic node')
        self.group = WaterGroup.objects.create(name='Synthetic line', node=self.node)
        self.account = Account.objects.create(number='SYN-201')
        Membership.objects.create(account=self.account, group=self.group, starts=self.today - timedelta(days=30))
        self.meter = Meter.objects.create(
            serial='SYN-METER-201', kind='individual', node=self.node,
            account=self.account, commissioned_on=self.today - timedelta(days=30),
        )
        self.resident = User.objects.create_user(username='resident-201', password='test-password')
        ResidentAccess.objects.create(
            user=self.resident, account=self.account, role='owner', starts=self.today - timedelta(days=30),
        )
        self.senior = User.objects.create_user(username='senior-201', password='test-password', is_staff=True)
        self.senior.user_permissions.add(Permission.objects.get(
            content_type__app_label='water', codename='use_controller_workspace',
        ))
        ControllerLineAccess.objects.create(
            user=self.senior, group=self.group, starts=self.today - timedelta(days=30),
        )
        self.admin = User.objects.create_superuser(username='admin-201', password='test-password')

    def submit_resident(self, value='1000.000'):
        self.client.force_login(self.resident)
        return self.client.post(
            f'/admin/cabinet/account/{self.account.pk}/meter/{self.meter.pk}/reading/',
            {'date': self.today.isoformat(), 'value': value, 'notes': 'Synthetic observation'},
        )

    def test_resident_submission_is_pending_observation_not_reading(self):
        self.assertEqual(self.submit_resident().status_code, 302)
        self.assertFalse(Reading.objects.exists())
        observation = ControllerReadingSubmission.objects.get()
        self.assertEqual(observation.source, observation.SOURCE_RESIDENT)
        self.assertEqual(observation.line_review_status, observation.LINE_REVIEW_PENDING)

    def test_sources_and_submitters_do_not_overwrite_each_other(self):
        self.submit_resident()
        ControllerReadingSubmission.objects.create(
            meter=self.meter, date=self.today, value=Decimal('999'), submitted_by=self.senior,
            source=ControllerReadingSubmission.SOURCE_LINE_SENIOR,
        )
        other = User.objects.create_user(username='controller-201')
        ControllerReadingSubmission.objects.create(
            meter=self.meter, date=self.today, value=Decimal('1001'), submitted_by=other,
            source=ControllerReadingSubmission.SOURCE_CONTROLLER,
        )
        self.submit_resident('1002.000')
        self.assertEqual(ControllerReadingSubmission.objects.count(), 3)
        self.assertEqual(
            ControllerReadingSubmission.objects.get(source='resident').value,
            Decimal('1002.000'),
        )

    def test_line_senior_can_flag_resident_observation_but_not_approve(self):
        self.submit_resident()
        observation = ControllerReadingSubmission.objects.get()
        self.login_as(self.senior)
        response = self.client.post('/admin/water/controller-workspace/', {
            'date': self.today.isoformat(), 'review_submission': observation.pk,
            'decision': 'flag', 'line_review_comment': 'Synthetic mismatch',
        })
        self.assertEqual(response.status_code, 302)
        observation.refresh_from_db()
        self.assertEqual(observation.line_review_status, observation.LINE_REVIEW_FLAGGED)
        self.assertEqual(observation.status, 'pending')
        self.assertFalse(Reading.objects.exists())

    def test_pending_line_review_blocks_admin_then_flagged_can_be_approved(self):
        self.submit_resident()
        observation = ControllerReadingSubmission.objects.get()
        self.login_as(self.admin)
        self.client.post(f'/admin/water/controllerreadingsubmission/{observation.pk}/approve/')
        observation.refresh_from_db()
        self.assertEqual(observation.status, 'pending')
        self.assertFalse(Reading.objects.exists())
        self.client.post(f'/admin/water/controllerreadingsubmission/{observation.pk}/reject/')
        observation.refresh_from_db()
        self.assertEqual(observation.status, 'pending')

        observation.line_review_status = observation.LINE_REVIEW_FLAGGED
        observation._change_reason = 'Synthetic line review'
        observation.save()
        self.client.post(f'/admin/water/controllerreadingsubmission/{observation.pk}/approve/')
        observation.refresh_from_db()
        self.assertEqual(observation.status, 'approved')
        self.assertEqual(observation.reading.value, Decimal('1000.000'))

    def test_backwards_observation_is_saved_and_flagged_for_admin(self):
        monday = self.today - timedelta(days=2)
        Reading.objects.create(meter=self.meter, date=monday, value=Decimal('1000'))
        observation = ControllerReadingSubmission.objects.create(
            meter=self.meter, date=self.today, value=Decimal('990'), submitted_by=self.senior,
            source=ControllerReadingSubmission.SOURCE_LINE_SENIOR,
        )
        self.assertTrue(observation.pk)
        self.assertTrue(any('ниже более раннего' in flag for flag in observation.chronology_flags()))
