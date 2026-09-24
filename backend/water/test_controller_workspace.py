from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .controller_scope import ControllerLineAccess
from .models import (
    Account,
    ControllerReadingSubmission,
    Membership,
    Meter,
    Reading,
    SupplyNode,
    User,
    WaterGroup,
)


class ControllerWorkspaceTests(TestCase):
    def setUp(self):
        call_command('setup_roles', stdout=StringIO())
        self.today = timezone.localdate()

        self.controller = User.objects.create_user(
            username='line-controller',
            password='test-password',
            is_staff=True,
        )
        self.controller.groups.add(Group.objects.get(name='Контролёр воды'))

        self.other_controller = User.objects.create_user(
            username='other-line-controller',
            password='test-password',
            is_staff=True,
        )
        self.other_controller.groups.add(Group.objects.get(name='Контролёр воды'))

        self.node = SupplyNode.objects.create(name='Синтетический узел')
        self.group = WaterGroup.objects.create(
            name='Синтетическая линия А',
            node=self.node,
            source='meter',
        )
        self.other_group = WaterGroup.objects.create(
            name='Синтетическая линия Б',
            node=self.node,
            source='meter',
        )

        self.account = Account.objects.create(number='SYN-1', plot='Тестовая улица 1')
        self.other_account = Account.objects.create(number='SYN-2', plot='Тестовая улица 2')
        Membership.objects.create(
            account=self.account,
            group=self.group,
            starts=self.today - timedelta(days=365),
        )
        Membership.objects.create(
            account=self.other_account,
            group=self.other_group,
            starts=self.today - timedelta(days=365),
        )

        self.line_meter = Meter.objects.create(
            serial='LINE-A',
            kind='line',
            node=self.node,
            group=self.group,
        )
        self.individual_meter = Meter.objects.create(
            serial='IND-A',
            kind='individual',
            node=self.node,
            account=self.account,
        )
        self.other_line_meter = Meter.objects.create(
            serial='LINE-B',
            kind='line',
            node=self.node,
            group=self.other_group,
        )
        self.other_individual_meter = Meter.objects.create(
            serial='IND-B',
            kind='individual',
            node=self.node,
            account=self.other_account,
        )
        self.main_meter = Meter.objects.create(
            serial='MAIN-TOP',
            kind='main',
            node=self.node,
        )

        previous_date = self.today - timedelta(days=30)
        Reading.objects.create(
            meter=self.line_meter,
            date=previous_date,
            value=Decimal('1000.000'),
        )
        Reading.objects.create(
            meter=self.individual_meter,
            date=previous_date,
            value=Decimal('400.000'),
        )
        Reading.objects.create(
            meter=self.other_line_meter,
            date=previous_date,
            value=Decimal('2000.000'),
        )
        Reading.objects.create(
            meter=self.other_individual_meter,
            date=previous_date,
            value=Decimal('900.000'),
        )
        Reading.objects.create(
            meter=self.main_meter,
            date=previous_date,
            value=Decimal('5000.000'),
        )

        self.access = ControllerLineAccess.objects.create(
            user=self.controller,
            group=self.group,
            starts=self.today - timedelta(days=365),
        )
        self.url = f'/admin/water/controller-workspace/?date={self.today.isoformat()}'

    def test_controller_role_uses_scoped_workspace_not_global_capture_permission(self):
        self.assertTrue(self.controller.has_perm('water.use_controller_workspace'))
        self.assertFalse(self.controller.has_perm('water.add_controllerreadingsubmission'))
        self.assertFalse(self.controller.has_perm('water.view_controllerreadingsubmission'))

    def test_controller_sees_only_assigned_line_and_never_main_meter(self):
        self.client.force_login(self.controller)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Синтетическая линия А')
        self.assertContains(response, 'Тестовая улица 1')
        self.assertContains(response, 'LINE-A')
        self.assertNotContains(response, 'Синтетическая линия Б')
        self.assertNotContains(response, 'Тестовая улица 2')
        self.assertNotContains(response, 'MAIN-TOP')

    def test_crafted_post_cannot_submit_meter_outside_assigned_line(self):
        self.client.force_login(self.controller)
        response = self.client.post(
            '/admin/water/controller-workspace/',
            {
                'date': self.today.isoformat(),
                f'value_{self.line_meter.pk}': '1100.000',
                f'value_{self.other_individual_meter.pk}': '999.000',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(ControllerReadingSubmission.objects.filter(
            meter=self.line_meter,
            submitted_by=self.controller,
            status='pending',
        ).exists())
        self.assertFalse(ControllerReadingSubmission.objects.filter(
            meter=self.other_individual_meter,
            submitted_by=self.controller,
        ).exists())

    def test_package_input_shows_preliminary_line_difference(self):
        self.client.force_login(self.controller)
        response = self.client.post(
            '/admin/water/controller-workspace/',
            {
                'date': self.today.isoformat(),
                f'value_{self.line_meter.pk}': '1100.000',
                f'value_{self.individual_meter.pk}': '480.000',
            },
        )
        self.assertEqual(response.status_code, 302)

        response = self.client.get(self.url)
        self.assertContains(response, '100.000')
        self.assertContains(response, '80.000')
        self.assertContains(response, '20.000')
        self.assertContains(response, 'Предварительный итог')

    def test_line_access_is_exclusive_for_overlapping_dates(self):
        with self.assertRaises(ValidationError):
            ControllerLineAccess.objects.create(
                user=self.other_controller,
                group=self.group,
                starts=self.today,
            )

    def test_expired_assignment_removes_line_from_workspace(self):
        self.access.ends = self.today
        self.access._change_reason = 'Синтетическое завершение доступа'
        self.access.save()

        self.client.force_login(self.controller)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'не закреплено ни одной линии')
        self.assertNotContains(response, 'LINE-A')

    def test_controller_cannot_submit_future_reading(self):
        self.client.force_login(self.controller)
        future = self.today + timedelta(days=1)
        response = self.client.post(
            '/admin/water/controller-workspace/',
            {
                'date': future.isoformat(),
                f'value_{self.line_meter.pk}': '1100.000',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Дата не может быть в будущем')
        self.assertEqual(ControllerReadingSubmission.objects.count(), 0)
