from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from .vtb_registry import VtbRegistryError, match_accounts, parse_debt_registry, parse_payment_registry


DEBT_SAMPLE = """0030142923;Иванов Иван Иванович;г.Новый, ул. Новая, дом 1, к.2, кв.1;0120;715.20
0030142632;Петров Петр Петрович;г.Новый, ул. Новая, дом 1, к.2, кв.2;0120;4449.37
0030143355;Сидоров Сидор Сидорови;г.Новый, ул. Новая, дом 1, к.2, кв.3;0120;1010.00"""

PAYMENT_SAMPLE = """27-01-2020;16-07-15;4;427285406;b9mvmzp3wgw5x8a6unk5unai7;0030142923;Иванов Иван Иванович;г.Новый, ул. Новая, дом 1, к.2, кв.1;0120;715.20;715.20;0.00
27-01-2020;20-11-00;4;427762567;gitrfmfkt673t2ne89gf794oo;0030142632;Петров Петр Петровчи;г.Новый, ул. Новая, дом 1, к.2, кв.2;0120;4449.37;4449.37;0.00
27-01-2020;21-33-00;2;427557890;lghef9936asdtpoij7xx19ytr;0030143355;Сидоров Сидор Сидорович;г.Новый, ул. Новая, дом 1, к.2, кв.3;0120;1010.00;1010.00;0.00
=3;6174.57;6174.57;0.00;8;28-01-2020"""


class VtbRegistryTests(SimpleTestCase):
    def test_real_debt_sample(self):
        rows = parse_debt_registry(DEBT_SAMPLE)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0].account_number, "0030142923")
        self.assertEqual(rows[1].amount, Decimal("4449.37"))

    def test_real_payment_sample_and_control(self):
        rows, control = parse_payment_registry(PAYMENT_SAMPLE)
        self.assertEqual(len(rows), 3)
        self.assertEqual(control.row_count, 3)
        self.assertEqual(control.operation_total, Decimal("6174.57"))
        self.assertEqual(rows[0].operation_id, "b9mvmzp3wgw5x8a6unk5unai7")

    def test_control_mismatch_fails_closed(self):
        with self.assertRaisesRegex(VtbRegistryError, "operation total mismatch"):
            parse_payment_registry(PAYMENT_SAMPLE.replace("=3;6174.57;", "=3;6174.58;"))

    def test_exact_account_matching_never_guesses_from_contact_data(self):
        a = SimpleNamespace(number="0030142923")
        duplicate = SimpleNamespace(number="0030142632")
        result = match_accounts(
            ["0030142923", "0030142632", "missing"],
            [a, duplicate, SimpleNamespace(number="0030142632")],
        )
        self.assertEqual([item[1] for item in result], ["matched", "ambiguous", "missing"])
        self.assertIs(result[0][2], a)
        self.assertIsNone(result[1][2])
