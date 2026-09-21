from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .models import Account, ControllerReadingSubmission, Meter, Reading, SupplyNode, User
from .templatetags.water_admin import controller_moderation_context


class ControllerModerationUXTests(TestCase):
    def setUp(self):
        call_command('setup_roles', stdout=StringIO())
        self.manager = User.objects.create_user(username='moderation-manager', is_staff=True)
        self.manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        self.controller = User.objects.create_user(username='moderation-controller', is_staff=True)
        self.controller.groups.add(Group.objects.get(name='Контролёр воды'))
        self.account = Account.objects.create(number='55', plot='Морская 17')
        self.node = SupplyNode.objects.create(name='Узел модерации')
        self.meter = Meter.objects.create(
            serial='MOD-55', kind='individual', node=self.node, account=self.account,
        )
        self.today = timezone.localdate()
        self.previous = Reading.objects.create(
            meter=self.meter, date=self.today - timedelta(days=30), value=Decimal('1100.000'),
        )
        self.submission = ControllerReadingSubmission.objects.create(
            meter=self.meter, date=self.today, value=Decimal('1183.000'),
            submitted_by=self.controller, notes='Контрольный обход',
        )

    def test_comparison_uses_previous_approved_reading(self):
        context = controller_moderation_context(self.submission)
        self.assertEqual(context['address'], 'Морская 17')
        self.assertEqual(context['previous'], self.previous)
        self.assertEqual(context['delta'], Decimal('83.000'))
        self.assertTrue(context['has_delta'])
        self.assertEqual(context['warning'], '')

    def test_negative_delta_is_flagged_before_approval(self):
        self.submission.value = Decimal('1099.000')
        context = controller_moderation_context(self.submission)
        self.assertEqual(context['delta'], Decimal('-1.000'))
        self.assertEqual(context['warning_level'], 'danger')
        self.assertIn('меньше предыдущего', context['warning'])

    def test_manager_sees_clear_actions_and_comparison_at_top(self):
        self.client.force_login(self.manager)
        url = f'/admin/water/controllerreadingsubmission/{self.submission.pk}/change/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Проверка показания')
        self.assertContains(response, 'Морская 17')
        self.assertContains(response, 'Предыдущее утверждённое')
        self.assertContains(response, 'Разница')
        self.assertContains(response, 'Принять показание')
        self.assertContains(response, 'Комментарий при отклонении (необязательно)')
        self.assertContains(response, 'Отклонить')
        self.assertNotContains(response, 'Причина (необязательно)')

    def test_queue_explains_how_to_moderate(self):
        self.client.force_login(self.manager)
        response = self.client.get('/admin/water/controllerreadingsubmission/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Как проверить показание')
        self.assertContains(response, 'Принять показание')

    def test_after_approval_page_links_to_reading_journal(self):
        self.client.force_login(self.manager)
        approve_url = f'/admin/water/controllerreadingsubmission/{self.submission.pk}/approve/'
        self.assertEqual(self.client.post(approve_url).status_code, 302)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.status, 'approved')
        self.assertIsNotNone(self.submission.reading_id)

        response = self.client.get(
            f'/admin/water/controllerreadingsubmission/{self.submission.pk}/change/'
        )
        self.assertContains(response, 'Показание принято и записано в журнал')
        self.assertContains(response, 'Открыть запись')
        self.assertContains(response, 'Открыть журнал показаний')
