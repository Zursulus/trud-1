import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from water.models import Account
from water.vtb_registry import VtbRegistryError, parse_payment_registry


class Command(BaseCommand):
    help = "Read-only VTB accepted-payments registry validation/matching. Never writes Payment rows."

    def add_arguments(self, parser):
        parser.add_argument("path")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        path = Path(options["path"])
        try:
            registry = parse_payment_registry(path.read_bytes())
        except (OSError, VtbRegistryError) as error:
            raise CommandError(str(error)) from error

        numbers = {row.personal_account for row in registry.rows}
        accounts = {
            account.number: account
            for account in Account.objects.filter(number__in=numbers).only("id", "number", "plot", "archived")
        }
        result_rows = []
        matched = 0
        for row in registry.rows:
            account = accounts.get(row.personal_account)
            status = "matched" if account is not None else "unmatched"
            if account is not None:
                matched += 1
            result_rows.append({
                "uni": row.uni,
                "bank_document": row.bank_document,
                "personal_account": row.personal_account,
                "amount": f"{row.operation_amount:.2f}",
                "paid_on": row.paid_on.isoformat(),
                "status": status,
                "account_id": account.pk if account else None,
                "account_archived": account.archived if account else None,
            })

        report = {
            "mode": "read-only-dry-run",
            "encoding": registry.encoding,
            "rows": len(registry.rows),
            "matched": matched,
            "unmatched": len(registry.rows) - matched,
            "operation_total": f"{registry.control.operation_total:.2f}",
            "transfer_total": f"{registry.control.transfer_total:.2f}",
            "commission_total": f"{registry.control.commission_total:.2f}",
            "payments": result_rows,
        }
        if options["as_json"]:
            self.stdout.write(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            self.stdout.write(
                "VTB DRY RUN: rows={rows} matched={matched} unmatched={unmatched} "
                "operation_total={operation_total} transfer_total={transfer_total} "
                "commission_total={commission_total}".format(**report)
            )
            for item in result_rows:
                self.stdout.write(
                    "{status}: account={personal_account} amount={amount} "
                    "date={paid_on} UNI={uni}".format(**item)
                )
        self.stdout.write("NO DATABASE WRITES PERFORMED")
