from decimal import Decimal

from django.test import SimpleTestCase

from .vtb_registry import VtbRegistryError, parse_payment_registry, render_debt_registry


SAMPLE = (
    "27-01-2020;16-07-15;4;427285406;abc123;ACC-001;Иванов Иван Иванович;"
    "г.Тест, ул. Первая, 1;0120;715.20;700.00;15.20\r\n"
    "27/01/2020;20:11:00;5;427762567;def456;ACC-002;Петров Петр Петрович;"
    "г.Тест, ул. Вторая, 2;0120;100.00;100.00;0.00\r\n"
    "=2;815.20;800.00;15.20;8;28-01-2020"
)


class VtbRegistryTests(SimpleTestCase):
    def test_parse_utf8_registry_and_reconcile_control_totals(self):
        registry = parse_payment_registry(SAMPLE.encode("utf-8"))
        self.assertEqual(len(registry.rows), 2)
        self.assertEqual(registry.rows[0].personal_account, "ACC-001")
        self.assertEqual(registry.rows[0].operation_amount, Decimal("715.20"))
        self.assertEqual(registry.control.transfer_total, Decimal("800.00"))

    def test_parse_win1251_registry(self):
        registry = parse_payment_registry(SAMPLE.encode("cp1251"), encoding="cp1251")
        self.assertEqual(registry.encoding, "cp1251")
        self.assertEqual(registry.rows[1].payer_name, "Петров Петр Петрович")

    def test_ambiguous_single_byte_encoding_requires_explicit_choice(self):
        with self.assertRaises(VtbRegistryError):
            parse_payment_registry(SAMPLE.encode("koi8-r"))
        registry = parse_payment_registry(SAMPLE.encode("koi8-r"), encoding="koi8-r")
        self.assertEqual(registry.rows[0].payer_name, "Иванов Иван Иванович")

    def test_control_mismatch_fails_closed(self):
        broken = SAMPLE.replace("=2;815.20", "=3;999.99")
        with self.assertRaises(VtbRegistryError):
            parse_payment_registry(broken.encode())

    def test_missing_control_row_fails_closed(self):
        with self.assertRaises(VtbRegistryError):
            parse_payment_registry(SAMPLE.split("\r\n")[0].encode())

    def test_invalid_uni_or_period_fails_closed(self):
        for broken in (
            SAMPLE.replace("abc123", "not-valid-uni!"),
            SAMPLE.replace(";0120;715.20", ";1320;715.20"),
        ):
            with self.subTest():
                with self.assertRaises(VtbRegistryError):
                    parse_payment_registry(broken.encode())

    def test_render_debt_registry_win1251(self):
        payload = render_debt_registry([{
            "personal_account": "ACC-001",
            "payer_name": "Иванов Иван Иванович",
            "payer_address": "г.Тест, ул. Первая, 1",
            "payment_period": "0120",
            "accrued_amount": "715.2",
        }])
        self.assertEqual(
            payload.decode("cp1251"),
            "ACC-001;Иванов Иван Иванович;г.Тест, ул. Первая, 1;0120;715.20",
        )

    def test_render_debt_registry_rejects_structural_characters(self):
        base = {
            "personal_account": "ACC-001",
            "payer_name": "Иванов Иван",
            "payer_address": "г.Тест, дом 1",
            "payment_period": "0120",
            "accrued_amount": "715.20",
        }
        for field, bad_value in (
            ("personal_account", "ACC;001"),
            ("payer_name", "Иванов\rИван"),
            ("payer_address", "г.Тест\nдом 1"),
        ):
            with self.subTest(field=field):
                row = dict(base)
                row[field] = bad_value
                with self.assertRaises(VtbRegistryError):
                    render_debt_registry([row])
