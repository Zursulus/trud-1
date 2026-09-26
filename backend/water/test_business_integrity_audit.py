from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from .models import Account, Meter, Reading, SupplyNode


class BusinessIntegrityAuditTests(TestCase):
    def test_clean_database_reports_zero_hard_violations_without_identifiers(self):
        output = StringIO()
        call_command('audit_business_integrity', '--strict', stdout=output)
        text = output.getvalue()
        self.assertIn('hard.reading_future=0', text)
        self.assertIn('hard.finance_cross_account_allocations=0', text)
        self.assertIn('review.reading_decrease_steps=0', text)
        self.assertIn('No PII, filenames or object identifiers were emitted.', text)

    def test_strict_mode_detects_future_reading_inserted_outside_model_save(self):
        account = Account.objects.create(number='AUDIT-1', plot='Синтетический участок')
        node = SupplyNode.objects.create(name='Синтетический узел аудита')
        meter = Meter.objects.create(
            serial='AUDIT-METER', kind='individual', node=node, account=account,
        )
        Reading.objects.bulk_create([
            Reading(
                meter=meter,
                date=timezone.localdate() + timedelta(days=1),
                value=1,
            )
        ])

        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('audit_business_integrity', '--strict', stdout=output)
        self.assertIn('hard.reading_future=1', output.getvalue())
