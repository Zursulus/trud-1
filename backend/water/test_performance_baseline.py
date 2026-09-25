from datetime import date, timedelta
from decimal import Decimal

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from .models import Account, Meter, Reading, ResidentAccess, SupplyNode, User
from .reading_admin_tools import _audit_readings


class PerformanceBaselineTests(TestCase):
    def test_resident_dashboard_query_budget_with_many_accounts(self):
        user = User.objects.create_user(
            username='performance-resident@example.test',
            password='test-password',
        )
        for index in range(12):
            account = Account.objects.create(
                number=f'PERF-{index:02d}',
                plot=f'Синтетический участок {index:02d}',
            )
            ResidentAccess.objects.create(
                user=user,
                account=account,
                role='owner',
                starts=date(2026, 1, 1),
            )

        self.client.force_login(user)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/admin/cabinet/')

        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(
            len(queries), 40,
            f'Кабинет выполнил слишком много SQL-запросов: {len(queries)}',
        )

    def test_reading_audit_query_budget_does_not_grow_per_row(self):
        account = Account.objects.create(number='PERF-READINGS', plot='Синтетический участок')
        node = SupplyNode.objects.create(name='Синтетический узел производительности')
        meter = Meter.objects.create(
            serial='PERF-METER', kind='individual', node=node, account=account,
        )
        start = date(2025, 1, 1)
        Reading.objects.bulk_create([
            Reading(
                meter=meter,
                date=start + timedelta(days=index),
                value=Decimal(index),
            )
            for index in range(250)
        ])

        with CaptureQueriesContext(connection) as queries:
            rows = _audit_readings()

        self.assertEqual(len(rows), 250)
        self.assertLessEqual(
            len(queries), 4,
            f'Аудит показаний выполнил слишком много SQL-запросов: {len(queries)}',
        )
