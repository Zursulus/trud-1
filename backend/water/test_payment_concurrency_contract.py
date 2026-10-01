"""Independent PostgreSQL oracles for two payments sharing one debt (#121)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from queue import Queue
from threading import Event
from time import monotonic
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import connection, connections, transaction
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from .billing import allocate_payment
from .models import Account, BillingPeriod, BillingPolicy, Charge, Payment, PaymentAllocation


class PostgreSQLConcurrencyMixin:
    def _concurrent_actions(self, model, object_id, operations):
        """Hold the shared object until both independent workers reach a real DB lock."""
        ready = Queue()

        def worker(operation):
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '25s'")
                    cursor.execute("SET statement_timeout = '30s'")
                    cursor.execute("SELECT pg_backend_pid()")
                    ready.put(cursor.fetchone()[0])
                return operation()
            finally:
                connections.close_all()

        # Join workers after releasing the blocker, including on assertion failure.
        with ThreadPoolExecutor(max_workers=2) as executor:
            with transaction.atomic():
                model.objects.select_for_update(of=("self",)).get(pk=object_id)
                deadline = monotonic() + 15
                futures = [executor.submit(worker, operation) for operation in operations]
                pids = [ready.get(timeout=max(0.001, deadline - monotonic())) for _ in futures]
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        # Activity snapshots otherwise remain fixed inside atomic().
                        cursor.execute("SELECT pg_stat_clear_snapshot()")
                        cursor.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE pid IN (%s, %s) AND wait_event_type = 'Lock'", pids)
                        if cursor.fetchone()[0] == 2:
                            break
                    if any(future.done() for future in futures):
                        self.fail("Worker completed before the shared-debt lock was released")
                    Event().wait(0.05)
                else:
                    self.fail("Both workers did not reach database locks within 15 seconds")
            return [future.result(timeout=30) for future in futures]


class PaymentAllocationParentStateTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        account = Account.objects.create(number="PARENT-STATE")
        period = BillingPeriod.objects.create(starts=today - timedelta(days=30), ends=today)
        self.charge = Charge.objects.create(account=account, period=period, kind="service", amount=Decimal("200.00"), status="approved")
        self.payment = Payment.objects.create(account=account, paid_on=today, amount=Decimal("150.00"), method="bank", status="confirmed")

    def test_cached_confirmed_payment_cannot_bypass_subsequent_reversal(self):
        current = Payment.objects.get(pk=self.payment.pk)
        current.status = "reversed"
        current.save()
        with self.assertRaises(ValidationError):
            PaymentAllocation.objects.create(payment=self.payment, charge=self.charge, amount=Decimal("150.00"))
        self.assertEqual((PaymentAllocation.objects.count(), PaymentAllocation.history.count()), (0, 0))
        self.assertEqual(Payment.objects.get(pk=self.payment.pk).status, "reversed")

    def test_cached_approved_charge_cannot_bypass_subsequent_cancellation(self):
        current = Charge.objects.get(pk=self.charge.pk)
        current.status = "cancelled"
        current.save()
        with self.assertRaises(ValidationError):
            PaymentAllocation.objects.create(payment=self.payment, charge=self.charge, amount=Decimal("150.00"))
        self.assertEqual((PaymentAllocation.objects.count(), PaymentAllocation.history.count()), (0, 0))
        self.assertEqual(Charge.objects.get(pk=self.charge.pk).status, "cancelled")


@skipUnless(connection.vendor == "postgresql", "Concurrent allocation contract requires PostgreSQL")
class PaymentConcurrencyContractTests(PostgreSQLConcurrencyMixin, TransactionTestCase):
    def setUp(self):
        today = timezone.localdate()
        account = Account.objects.create(number="CONCURRENT-PAYMENTS")
        period = BillingPeriod.objects.create(starts=today - timedelta(days=30), ends=today)
        BillingPolicy.objects.create(name="Synthetic oldest allocation", is_default=True, payment_allocation="oldest")
        self.charge = Charge.objects.create(account=account, period=period, kind="service", amount=Decimal("200.00"), status="approved")
        self.payments = [Payment.objects.create(account=account, paid_on=today, amount=Decimal("150.00"), method="bank", status="confirmed") for _ in range(2)]

    def _concurrent_allocations(self, operation):
        return self._concurrent_actions(Charge, self.charge.pk, [
            lambda pk=payment.pk: operation(pk) for payment in self.payments
        ])

    def test_manual_competing_allocations_reject_excess_without_partial_write(self):
        def manual(payment_id):
            allocation = PaymentAllocation(payment_id=payment_id, charge_id=self.charge.pk, amount=Decimal("150.00"))
            try:
                allocation.save()
            except ValidationError:
                return "rejected"
            return "saved"

        self.assertEqual(sorted(self._concurrent_allocations(manual)), ["rejected", "saved"])
        allocation = PaymentAllocation.objects.get()
        self.assertEqual(allocation.amount, Decimal("150.00"))
        self.assertEqual(allocation.history.count(), 1)
        self.assertEqual(list(Payment.objects.order_by("pk").values_list("status", flat=True)), ["confirmed", "confirmed"])
        unused = next(payment for payment in self.payments if payment.pk != allocation.payment_id)
        # A deliberate, valid retry uses the remaining 50; never silently shrink 150.
        PaymentAllocation.objects.create(payment=unused, charge=self.charge, amount=Decimal("50.00"))
        self.assertEqual(PaymentAllocation.objects.aggregate(total=Sum("amount"))["total"], Decimal("200.00"))

    def test_automatic_competing_payments_use_exact_remaining_debt(self):
        def automatic(payment_id):
            allocations, _ = allocate_payment(Payment.objects.get(pk=payment_id))
            return sum((item.amount for item in allocations), Decimal("0.00"))

        self.assertEqual(sorted(self._concurrent_allocations(automatic)), [Decimal("50.00"), Decimal("150.00")])
        self.assertEqual(PaymentAllocation.objects.aggregate(total=Sum("amount"))["total"], Decimal("200.00"))
        self.assertEqual(PaymentAllocation.objects.count(), 2)
        self.assertEqual(list(Payment.objects.order_by("pk").values_list("status", flat=True)), ["confirmed", "confirmed"])

    def test_manual_and_automatic_paths_share_the_same_debt_lock(self):
        def mixed(payment_id):
            if payment_id == self.payments[0].pk:
                try:
                    PaymentAllocation.objects.create(payment_id=payment_id, charge_id=self.charge.pk, amount=Decimal("150.00"))
                except ValidationError:
                    return "rejected", Decimal("0.00")
                return "saved", Decimal("150.00")
            allocations, _ = allocate_payment(Payment.objects.get(pk=payment_id))
            return "automatic", sum((item.amount for item in allocations), Decimal("0.00"))

        results = self._concurrent_allocations(mixed)
        # Either serial order is valid; each has an independently stated outcome.
        self.assertIn(results, [
            [("saved", Decimal("150.00")), ("automatic", Decimal("50.00"))],
            [("rejected", Decimal("0.00")), ("automatic", Decimal("150.00"))],
        ])
        expected = Decimal("200.00") if results[0][0] == "saved" else Decimal("150.00")
        self.assertEqual(PaymentAllocation.objects.aggregate(total=Sum("amount"))["total"], expected)
        self.assertEqual(PaymentAllocation.history.count(), 2 if results[0][0] == "saved" else 1)
        self.assertEqual(list(Payment.objects.order_by("pk").values_list("status", flat=True)), ["confirmed", "confirmed"])
