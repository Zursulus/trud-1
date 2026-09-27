from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
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


class StaffWorkspaceWaterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", stdout=StringIO())
        cls.today = timezone.localdate()

        cls.manager = User.objects.create_user(username="water-hub-manager", is_staff=True)
        cls.manager.groups.add(Group.objects.get(name="Администратор ТСН"))
        cls.operator = User.objects.create_user(username="water-hub-operator", is_staff=True)
        cls.operator.groups.add(Group.objects.get(name="Оператор воды"))
        cls.controller = User.objects.create_user(username="water-hub-controller", is_staff=True)
        cls.controller.groups.add(Group.objects.get(name="Контролёр воды"))
        cls.private_user = User.objects.create_user(username="water-hub-private", is_staff=True)
        cls.private_user.groups.add(Group.objects.get(name="Закрытый реестр членов ТСН"))
        cls.resident = User.objects.create_user(username="water-hub-resident")

        cls.node = SupplyNode.objects.create(name="Water Hub узел")
        cls.group_a = WaterGroup.objects.create(name="Water Hub линия A", node=cls.node, source="meter")
        cls.group_b = WaterGroup.objects.create(name="Water Hub линия B", node=cls.node, source="meter")

        cls.account_a = Account.objects.create(
            number="WH-101",
            plot="Садовая 101",
            contact_name="Секретный Контакт 101",
            phone="+79990000101",
        )
        cls.account_b = Account.objects.create(
            number="WH-202",
            plot="Лесная 202",
            contact_name="Секретный Контакт 202",
            phone="+79990000202",
        )
        Membership.objects.create(
            account=cls.account_a, group=cls.group_a, starts=cls.today - timedelta(days=30),
        )
        Membership.objects.create(
            account=cls.account_b, group=cls.group_b, starts=cls.today - timedelta(days=30),
        )
        ControllerLineAccess.objects.create(
            user=cls.controller, group=cls.group_a, starts=cls.today - timedelta(days=30),
        )

        cls.meter_a = Meter.objects.create(
            serial="WH-METER-101", kind="individual", node=cls.node, account=cls.account_a,
        )
        cls.meter_b = Meter.objects.create(
            serial="WH-METER-202", kind="individual", node=cls.node, account=cls.account_b,
        )
        Meter.objects.create(
            serial="WH-LINE-A", kind="line", node=cls.node, group=cls.group_a,
        )
        Reading.objects.create(
            meter=cls.meter_a, date=cls.today, value=Decimal("123.456"),
        )

        cls.admin_ready = ControllerReadingSubmission.objects.create(
            meter=cls.meter_a,
            date=cls.today,
            value=Decimal("124.000"),
            source=ControllerReadingSubmission.SOURCE_CONTROLLER,
            status="pending",
            line_review_status=ControllerReadingSubmission.LINE_REVIEW_NOT_REQUIRED,
            submitted_by=cls.operator,
        )
        cls.line_review_a = ControllerReadingSubmission.objects.create(
            meter=cls.meter_a,
            date=cls.today,
            value=Decimal("124.100"),
            source=ControllerReadingSubmission.SOURCE_RESIDENT,
            status="pending",
            line_review_status=ControllerReadingSubmission.LINE_REVIEW_PENDING,
            submitted_by=cls.resident,
        )
        cls.line_review_b = ControllerReadingSubmission.objects.create(
            meter=cls.meter_b,
            date=cls.today,
            value=Decimal("224.100"),
            source=ControllerReadingSubmission.SOURCE_RESIDENT,
            status="pending",
            line_review_status=ControllerReadingSubmission.LINE_REVIEW_PENDING,
            submitted_by=cls.resident,
        )
        cls.controller_pending = ControllerReadingSubmission.objects.create(
            meter=cls.meter_a,
            date=cls.today - timedelta(days=1),
            value=Decimal("122.900"),
            source=ControllerReadingSubmission.SOURCE_LINE_SENIOR,
            status="pending",
            line_review_status=ControllerReadingSubmission.LINE_REVIEW_NOT_REQUIRED,
            submitted_by=cls.controller,
        )

    def login(self, user):
        self.client.force_login(user)

    def test_manager_sees_final_review_queue_without_pii(self):
        self.login(self.manager)
        response = self.client.get("/work/water/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "На финальной проверке")
        self.assertContains(response, "WH-METER-101")
        self.assertContains(response, "Премодерация показаний")
        self.assertNotContains(response, "Секретный Контакт 101")
        self.assertNotContains(response, "+79990000101")
        self.assertNotContains(response, "water-hub-resident")

    def test_operator_sees_operational_water_actions_but_not_moderation_queue(self):
        self.login(self.operator)
        response = self.client.get("/work/water/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Внести показания")
        self.assertContains(response, "Журнал показаний")
        self.assertContains(response, "Счётчики")
        self.assertContains(response, "Водный баланс и потери")
        self.assertNotContains(response, "На финальной проверке")
        self.assertNotContains(response, "Ждут проверки старшего линии")
        self.assertNotContains(response, "Секретный Контакт")

    def test_controller_queue_is_strictly_limited_to_assigned_line(self):
        self.login(self.controller)
        response = self.client.get("/work/water/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Внести и сверить показания")
        self.assertContains(response, "WH-101")
        self.assertContains(response, "WH-METER-101")
        self.assertContains(response, "Ждут моей сверки")
        self.assertNotContains(response, "WH-202")
        self.assertNotContains(response, "WH-METER-202")
        self.assertNotContains(response, "Секретный Контакт 101")
        self.assertNotContains(response, "+79990000101")
        self.assertEqual(response.context["line_review_count"], 1)
        self.assertEqual(response.context["own_pending_count"], 1)

    def test_private_registry_cannot_open_water_hub(self):
        self.login(self.private_user)
        response = self.client.get("/work/water/")
        self.assertEqual(response.status_code, 403)

    def test_controller_dashboard_attention_is_scoped_and_points_to_water_hub(self):
        self.login(self.controller)
        response = self.client.get("/work/")
        self.assertEqual(response.status_code, 200)
        matching = [item for item in response.context["attention"] if item["label"] == "Наблюдения жителей на сверке"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["count"], 1)
        self.assertEqual(matching[0]["url"], "/work/water/")

    def test_water_hub_query_counts_are_bounded(self):
        self.login(self.manager)
        with CaptureQueriesContext(connection) as manager_queries:
            response = self.client.get("/work/water/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(manager_queries), 24, len(manager_queries))

        self.client.logout()
        self.login(self.controller)
        with CaptureQueriesContext(connection) as controller_queries:
            response = self.client.get("/work/water/")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(controller_queries), 28, len(controller_queries))
