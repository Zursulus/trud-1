from io import StringIO

from django.contrib import admin
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import RequestFactory, TestCase

from water.models import Account, ImportRow, LandPlot, Person, PlotRelation, User
from water.private_registry import MemberRegistryEntry
from water.resident_numbers import ResidentNumberSlot


ADMIN_GROUP = 'Администратор ТСН'
PRIVATE_GROUP = 'Закрытый реестр членов ТСН'


class PrivateRegistryBoundaryTests(TestCase):
    def setUp(self):
        call_command('setup_roles', stdout=StringIO())
        self.factory = RequestFactory()
        self.admin_user = User.objects.create_user(
            username='regular-admin', password='test-password', is_staff=True,
        )
        self.admin_user.groups.add(Group.objects.get(name=ADMIN_GROUP))
        self.private_user = User.objects.create_user(
            username='private-registry', password='test-password', is_staff=True,
        )
        self.private_user.groups.add(Group.objects.get(name=PRIVATE_GROUP))

    def request_for(self, user):
        request = self.factory.get('/admin/')
        request.user = user
        return request

    def test_regular_admin_does_not_receive_private_registry_permissions(self):
        codenames = set(
            Group.objects.get(name=ADMIN_GROUP).permissions.values_list('codename', flat=True)
        )
        self.assertNotIn('access_private_registry', codenames)
        self.assertNotIn('view_person', codenames)
        self.assertNotIn('view_plotrelation', codenames)
        self.assertNotIn('view_importrow', codenames)
        self.assertNotIn('view_importbatch', codenames)

    def test_private_registry_group_is_explicit_and_narrow(self):
        codenames = set(
            Group.objects.get(name=PRIVATE_GROUP).permissions.values_list('codename', flat=True)
        )
        self.assertIn('access_private_registry', codenames)
        self.assertIn('view_person', codenames)
        self.assertIn('view_plotrelation', codenames)
        self.assertIn('view_importrow', codenames)
        self.assertIn('view_account', codenames)
        self.assertIn('view_landplot', codenames)
        self.assertNotIn('change_payment', codenames)
        self.assertNotIn('add_reading', codenames)

    def test_member_registry_maps_only_real_resident_numbers(self):
        person = Person.objects.create(full_name='Тест Реестра')
        slot = ResidentNumberSlot.objects.get(pk=1)
        entry = MemberRegistryEntry.objects.create(resident_number=slot, person=person)
        self.assertEqual(entry.resident_number_id, 1)

        test_person = Person.objects.create(full_name='Нельзя Связать')
        test_slot = ResidentNumberSlot.objects.get(pk=333)
        with self.assertRaises(ValidationError):
            MemberRegistryEntry.objects.create(resident_number=test_slot, person=test_person)

    def test_private_mapping_cannot_be_silently_reassigned(self):
        first = Person.objects.create(full_name='Первый Человек')
        second = Person.objects.create(full_name='Второй Человек')
        entry = MemberRegistryEntry.objects.create(
            resident_number=ResidentNumberSlot.objects.get(pk=2), person=first,
        )
        entry.person = second
        with self.assertRaisesMessage(ValidationError, 'нельзя переписывать'):
            entry.save()

    def test_regular_admin_cannot_open_person_or_import_pii_admin(self):
        request = self.request_for(self.admin_user)
        self.assertFalse(admin.site._registry[Person].has_view_permission(request))
        self.assertFalse(admin.site._registry[PlotRelation].has_view_permission(request))
        self.assertFalse(admin.site._registry[ImportRow].has_view_permission(request))

        private_request = self.request_for(self.private_user)
        self.assertTrue(admin.site._registry[Person].has_view_permission(private_request))
        self.assertTrue(admin.site._registry[PlotRelation].has_view_permission(private_request))
        self.assertTrue(admin.site._registry[ImportRow].has_view_permission(private_request))
        self.assertTrue(admin.site._registry[MemberRegistryEntry].has_view_permission(private_request))

    def test_operational_account_admin_hides_legacy_contact_fields(self):
        model_admin = admin.site._registry[Account]
        request = self.request_for(self.admin_user)
        self.assertNotIn('contact_name', model_admin.get_list_display(request))
        self.assertNotIn('phone', model_admin.get_list_display(request))
        self.assertNotIn('contact_name', model_admin.get_search_fields(request))
        self.assertNotIn('phone', model_admin.get_search_fields(request))
        self.assertIn('contact_name', model_admin.get_exclude(request))
        self.assertIn('phone', model_admin.get_exclude(request))

        private_request = self.request_for(self.private_user)
        self.assertIn('contact_name', model_admin.get_list_display(private_request))
        self.assertIn('phone', model_admin.get_list_display(private_request))
        self.assertIn('contact_name', model_admin.get_readonly_fields(private_request))
        self.assertIn('phone', model_admin.get_readonly_fields(private_request))

    def test_landplot_list_hides_people_without_private_permission(self):
        model_admin = admin.site._registry[LandPlot]
        self.assertNotIn('current_people', model_admin.get_list_display(self.request_for(self.admin_user)))
        self.assertIn('current_people', model_admin.get_list_display(self.request_for(self.private_user)))

    def test_test_user_333_has_no_name_or_email(self):
        account = Account.objects.create(number='TEST-333', plot='Тестовый участок №333')
        output = StringIO()
        call_command(
            'provision_resident_test',
            account_id=account.pk,
            username='test333',
            stdout=output,
        )
        slot = ResidentNumberSlot.objects.select_related('user').get(pk=333)
        self.assertEqual(slot.user.first_name, '')
        self.assertEqual(slot.user.last_name, '')
        self.assertEqual(slot.user.email, '')
