from datetime import timedelta
from decimal import Decimal
from io import StringIO
from urllib.parse import urlencode

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from .controller_scope import ControllerLineAccess
from .models import (
    Account,
    BillingPeriod,
    Charge,
    LandPlot,
    Membership,
    Meter,
    Payment,
    Person,
    Reading,
    SupplyNode,
    User,
    WaterGroup,
)


class StaffWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.today = timezone.localdate()

        cls.manager = User.objects.create_user(username="workspace-manager", is_staff=True)
        cls.manager.groups.add(Group.objects.get(name="Администратор ТСН"))
        cls.operator = User.objects.create_user(username="workspace-operator", is_staff=True)
        cls.operator.groups.add(Group.objects.get(name="Оператор воды"))
        cls.controller = User.objects.create_user(username="workspace-controller", is_staff=True)
        cls.controller.groups.add(Group.objects.get(name="Контролёр воды"))
        cls.private_user = User.objects.create_user(username="workspace-private", is_staff=True)
        cls.private_user.groups.add(Group.objects.get(name="Закрытый реестр членов ТСН"))

        cls.node = SupplyNode.objects.create(name="Узел workspace")
        cls.group_a = WaterGroup.objects.create(name="Линия workspace A", node=cls.node, source="meter")
        cls.group_b = WaterGroup.objects.create(name="Линия workspace B", node=cls.node, source="meter")

        cls.account_a = Account.objects.create(
            number="WS-101", plot="Садовая 101",
            contact_name="Секретное ФИО 101", phone="+79990000101",
        )
        cls.account_b = Account.objects.create(
            number="WS-202", plot="Лесная 202",
            contact_name="Секретное ФИО 202", phone="+79990000202",
        )
        LandPlot.objects.create(label="Участок 101", address="Кадастровый ориентир 101", account=cls.account_a)
        LandPlot.objects.create(label="Участок 202", address="Кадастровый ориентир 202", account=cls.account_b)
        Person.objects.create(full_name="Очень Секретный Человек", phone="+79991112233")

        Membership.objects.create(account=cls.account_a, group=cls.group_a, starts=cls.today - timedelta(days=30))
        Membership.objects.create(account=cls.account_b, group=cls.group_b, starts=cls.today - timedelta(days=30))
        ControllerLineAccess.objects.create(
            user=cls.controller, group=cls.group_a, starts=cls.today - timedelta(days=30),
        )

        cls.meter_a = Meter.objects.create(
            serial="WS-METER-101", kind="individual", node=cls.node, account=cls.account_a,
        )
        Reading.objects.create(meter=cls.meter_a, date=cls.today - timedelta(days=1), value=Decimal("123.456"))

        period = BillingPeriod.objects.create(
            starts=cls.today - timedelta(days=31), ends=cls.today - timedelta(days=1), status="open",
        )
        Charge.objects.create(
            account=cls.account_a, period=period, kind="service", amount=Decimal("1000.00"), status="approved",
        )
        Payment.objects.create(
            account=cls.account_a, paid_on=cls.today, amount=Decimal("400.00"), method="bank", status="confirmed",
        )

    def login(self, user):
        self.client.force_login(user)

    def test_anonymous_user_is_redirected_to_staff_login(self):
        response = self.client.get("/work/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    def test_search_account_and_return_preserve_the_query(self):
        self.login(self.manager)
        query = "Садовая 101"
        response = self.client.get("/work/search/", {"q": query})
        self.assertContains(response, f'/work/accounts/{self.account_a.pk}/?q=')
        card = self.client.get(f"/work/accounts/{self.account_a.pk}/", {"q": query})
        self.assertEqual(card.context["account_back_url"], "/work/search/?" + urlencode({"q": query}))
        self.assertContains(card, "К результатам поиска")
        results = self.client.get(card.context["account_back_url"])
        self.assertEqual(results.context["q"], query)
        self.assertEqual([account.pk for account in results.context["results"]], [self.account_a.pk])

    def test_account_return_cannot_be_redirected_by_untrusted_next_or_query(self):
        self.login(self.manager)
        raw_query = "  101  &next=https://example.test/ " + "1" * 200
        query = " ".join(raw_query.split())[:160]
        card = self.client.get(f"/work/accounts/{self.account_a.pk}/", {
            "q": raw_query, "next": "https://example.test/",
        })
        self.assertEqual(card.context["account_back_url"], "/work/search/?" + urlencode({"q": query}))
        self.assertEqual(card.context["account_edit_url"], f"/work/accounts/{self.account_a.pk}/edit/?" + urlencode({"q": query}))

    def test_manager_searches_and_opens_account_without_pii(self):
        self.login(self.manager)
        response = self.client.get("/work/search/", {"q": "Кадастровый ориентир 101"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "WS-101")
        self.assertContains(response, "Участок 101")
        self.assertNotContains(response, "Секретное ФИО 101")
        self.assertNotContains(response, "+79990000101")
        self.assertNotContains(response, "Очень Секретный Человек")

        card = self.client.get(f"/work/accounts/{self.account_a.pk}/")
        self.assertEqual(card.status_code, 200)
        self.assertContains(card, "Садовая 101")
        self.assertContains(card, "Кадастровый ориентир 101")
        self.assertContains(card, "WS-METER-101")
        self.assertContains(card, "600,00 ₽")
        self.assertContains(card, "Финансы")
        self.assertNotContains(card, "Секретное ФИО 101")
        self.assertNotContains(card, "+79990000101")

    def test_operator_gets_water_context_but_no_finance_landplot_or_pii(self):
        self.login(self.operator)
        response = self.client.get(f"/work/accounts/{self.account_a.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "WS-METER-101")
        self.assertContains(response, "Линия workspace A")
        self.assertNotContains(response, "Кадастровый ориентир 101")
        self.assertNotContains(response, "Финансы")
        self.assertNotContains(response, "600,00 ₽")
        self.assertNotContains(response, "Секретное ФИО 101")
        self.assertNotContains(response, "+79990000101")

    def test_controller_is_limited_to_line_scope_and_water_fields(self):
        self.login(self.controller)
        allowed = self.client.get(f"/work/accounts/{self.account_a.pk}/")
        denied = self.client.get(f"/work/accounts/{self.account_b.pk}/")
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(denied.status_code, 404)
        self.assertContains(allowed, "WS-METER-101")
        self.assertContains(allowed, "Линия workspace A")
        self.assertNotContains(allowed, "Кадастровый ориентир 101")

        search = self.client.get("/work/search/", {"q": "WS-METER-101"})
        self.assertContains(search, "WS-101")
        self.assertNotContains(search, "WS-202")
        self.assertNotContains(search, "Финансы")

    def test_private_registry_gets_landplot_but_no_water_or_finance_capability(self):
        self.login(self.private_user)
        response = self.client.get(f"/work/accounts/{self.account_a.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Кадастровый ориентир 101")
        self.assertNotContains(response, "WS-METER-101")
        self.assertNotContains(response, "Линия workspace A")
        self.assertNotContains(response, "Финансы")
        self.assertNotContains(response, "600,00 ₽")

        meter_search = self.client.get("/work/search/", {"q": "WS-METER-101"})
        self.assertNotContains(meter_search, "WS-101")

    def test_archived_account_is_visibly_marked(self):
        archived = Account.objects.create(number="WS-ARCH", plot="Архивный участок", archived=True)
        self.login(self.manager)
        response = self.client.get(f"/work/accounts/{archived.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Архивная карточка")
        self.assertContains(response, "Архивный объект")

    def test_existing_django_admin_remains_available(self):
        self.login(self.manager)
        self.assertEqual(self.client.get("/admin/").status_code, 200)
        self.assertEqual(self.client.get("/work/").status_code, 200)

    def test_search_and_account_card_have_bounded_query_counts(self):
        self.login(self.manager)
        with CaptureQueriesContext(connection) as search_queries:
            response = self.client.get("/work/search/", {"q": "WS-101"})
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(search_queries), 16, len(search_queries))

        with CaptureQueriesContext(connection) as card_queries:
            response = self.client.get(f"/work/accounts/{self.account_a.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(card_queries), 24, len(card_queries))
