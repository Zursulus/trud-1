"""Resident recovery/navigation contracts, using synthetic accounts only."""
from datetime import timedelta
from decimal import Decimal
from html.parser import HTMLParser
from importlib import import_module
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.contrib import messages
from django.contrib.messages.storage.base import Message
from django.contrib.messages.storage.session import SessionStorage
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    Account, BillingPeriod, Charge, ControllerReadingSubmission, Meter, Person,
    ResidentAccess, SupplyNode, User,
)
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity


class PageElements(HTMLParser):
    def __init__(self, response):
        super().__init__()
        self.ids = set()
        self.links = []
        self.forms = []
        self.hidden = {}
        self.buttons = []
        self.feed(response.content.decode())

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if attrs.get('id'):
            self.ids.add(attrs['id'])
        if tag == 'a':
            self.links.append(attrs.get('href', ''))
        elif tag == 'form':
            self.forms.append(attrs)
        elif tag == 'input' and attrs.get('type') == 'hidden':
            self.hidden[attrs.get('name')] = attrs.get('value')
        elif tag == 'button':
            self.buttons.append(attrs)


class ResidentFlowPolishTests(TestCase):
    password = 'Synthetic-resident-flow-2026!'

    def setUp(self):
        self.today = timezone.localdate()
        self.account = Account.objects.create(number='FLOW-A', plot='Synthetic plot A')
        self.other = Account.objects.create(number='FLOW-B', plot='Synthetic other plot B')
        self.user = User.objects.create_user(
            username='flow-resident@example.test', email='flow-resident@example.test',
            password=self.password,
        )
        self.access = ResidentAccess.objects.create(
            user=self.user, account=self.account, role='owner',
            starts=self.today - timedelta(days=30),
        )
        node = SupplyNode.objects.create(name='Synthetic flow node')
        self.meter = Meter.objects.create(
            serial='FLOW-METER-A', kind='individual', node=node, account=self.account,
        )
        self.other_meter = Meter.objects.create(
            serial='FLOW-METER-B', kind='individual', node=node, account=self.other,
        )
        period = BillingPeriod.objects.create(
            starts=self.today - timedelta(days=30), ends=self.today + timedelta(days=1),
        )
        Charge.objects.create(
            account=self.account, period=period, kind='service',
            amount=Decimal('123.00'), status='approved',
        )
        self.water_url = reverse('resident_water', args=[self.account.pk])
        self.reading_url = reverse('resident_reading', args=[self.account.pk, self.meter.pk])
        self.client.force_login(self.user)

    def grant(self, **capabilities):
        staff = User.objects.create_user(username='flow-verifier', is_staff=True)
        person = Person.objects.create(full_name='Synthetic flow resident')
        ResidentIdentity.objects.create(
            user=self.user, person=person, verified_by=staff, basis='Synthetic identity',
        )
        return PortalGrant.objects.create(
            person=person, account=self.account, starts=self.today - timedelta(days=1),
            verified_by=staff, basis='Synthetic bounded grant', **capabilities,
        )

    def test_reopening_own_reading_url_returns_to_water_without_submission(self):
        response = self.client.get(self.reading_url)
        self.assertRedirects(response, self.water_url)
        self.assertFalse(ControllerReadingSubmission.objects.exists())

    def test_reading_get_keeps_account_meter_and_capability_guards(self):
        self.grant(can_view_account=True, can_submit_water=False)
        for account_id, meter_id in (
            (self.account.pk, self.meter.pk),
            (self.account.pk, self.other_meter.pk),
            (self.other.pk, self.other_meter.pk),
        ):
            with self.subTest(account=account_id, meter=meter_id):
                response = self.client.get(reverse('resident_reading', args=[account_id, meter_id]))
                self.assertEqual(response.status_code, 404)
        self.assertFalse(ControllerReadingSubmission.objects.exists())

    def test_authorized_reading_get_ignores_values_and_still_denies_wrong_or_ended_targets(self):
        existing = ControllerReadingSubmission.objects.create(
            meter=self.meter, submitted_by=self.user, source='resident',
            date=self.today, value=Decimal('10.000'),
        )
        response = self.client.get(self.reading_url, {
            'date': self.today.isoformat(), 'value': '99.000', 'notes': 'GET is not a submission',
        })
        self.assertRedirects(response, self.water_url)
        main = Meter.objects.create(serial='FLOW-MAIN', kind='main', node=self.meter.node)
        for account_id, meter_id in (
            (self.account.pk, self.other_meter.pk),
            (self.other.pk, self.other_meter.pk),
            (self.account.pk, main.pk),
        ):
            with self.subTest(account=account_id, meter=meter_id):
                self.assertEqual(self.client.get(reverse('resident_reading', args=[account_id, meter_id])).status_code, 404)
        self.access.ends = self.today
        self.access.save()
        self.assertEqual(self.client.get(self.reading_url).status_code, 404)
        existing.refresh_from_db()
        self.assertEqual(existing.value, Decimal('10.000'))
        self.assertEqual(existing.history.count(), 1)
        self.assertEqual(ControllerReadingSubmission.objects.count(), 1)

    def test_interrupted_submission_can_login_and_return_without_replaying_post(self):
        existing = ControllerReadingSubmission.objects.create(
            meter=self.meter, submitted_by=self.user, source='resident',
            date=self.today, value=Decimal('10.000'),
        )
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        page = PageElements(client.get(self.water_url))
        token = page.hidden['csrfmiddlewaretoken']
        # Expire the server-side session while retaining the browser's CSRF cookie.
        session_class = import_module(settings.SESSION_ENGINE).SessionStore
        session_class(session_key=client.session.session_key).delete()
        interrupted = client.post(self.reading_url, {
            'date': self.today.isoformat(), 'value': '11.234', 'notes': 'Interrupted input',
            'csrfmiddlewaretoken': token,
        })
        self.assertEqual(interrupted.status_code, 302)
        location = urlsplit(interrupted.url)
        self.assertEqual(location.path, reverse('resident_login'))
        next_path = parse_qs(location.query)['next'][0]
        returned = client.post(reverse('resident_login'), {
            'username': self.user.username, 'password': self.password, 'next': next_path,
            'csrfmiddlewaretoken': token,
        }, follow=True)
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(returned.request['PATH_INFO'], self.water_url)
        self.assertContains(returned, self.meter.serial)
        existing.refresh_from_db()
        self.assertEqual(existing.value, Decimal('10.000'))
        self.assertEqual(existing.history.count(), 1)
        self.assertEqual(ControllerReadingSubmission.objects.count(), 1)

    def test_ended_access_page_provides_working_csrf_logout(self):
        self.access.ends = self.today
        self.access.save()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.get(reverse('resident_dashboard'))
        self.assertEqual(response.status_code, 403)
        page = PageElements(response)
        logout_url = reverse('resident_logout')
        self.assertTrue(any(form.get('action') == logout_url and form.get('method') == 'post'
                            for form in page.forms))
        token = page.hidden['csrfmiddlewaretoken']
        logged_out = client.post(logout_url, {'csrfmiddlewaretoken': token}, follow=True)
        self.assertEqual(logged_out.status_code, 200)
        self.assertFalse(logged_out.wsgi_request.user.is_authenticated)
        self.assertNotContains(logged_out, self.account.plot)

    def test_finance_navigation_has_real_sections_without_payment_or_download_claims(self):
        response = self.client.get(reverse('resident_payments', args=[self.account.pk]))
        page = PageElements(response)
        for section in ('charges', 'payments'):
            self.assertIn(section, page.ids)
            self.assertIn('#' + section, page.links)
        self.assertFalse(any(button.get('type') == 'button' for button in page.buttons))
        self.assertNotContains(response, 'Перейти к оплате')
        self.assertNotContains(response, 'Скачать справку об оплатах')
        self.assertContains(response, 'Подтверждённых оплат пока нет.')
        self.assertContains(response, '123,00')

    def test_finance_without_appeal_or_document_rights_has_no_forbidden_links(self):
        self.grant(can_view_account=True, can_view_finance=True)
        response = self.client.get(reverse('resident_payments', args=[self.account.pk]))
        self.assertEqual(response.status_code, 200)
        page = PageElements(response)
        for destination in ('resident_appeal_new', 'resident_documents'):
            self.assertNotIn(reverse(destination, args=[self.account.pk]), page.links)
            self.assertEqual(self.client.get(reverse(destination, args=[self.account.pk])).status_code, 404)
        self.assertIn(reverse('resident_access_help'), page.links)

    def test_security_without_appeal_rights_offers_help_instead_of_forbidden_action(self):
        self.grant(can_view_account=True)
        response = self.client.get(reverse('resident_security', args=[self.account.pk]))
        page = PageElements(response)
        self.assertNotIn(reverse('resident_appeal_new', args=[self.account.pk]), page.links)
        self.assertIn(reverse('resident_access_help'), page.links)
        self.assertIn(reverse('resident_password_change'), page.links)

    def test_security_keeps_appeal_action_for_authorized_resident(self):
        response = self.client.get(reverse('resident_security', args=[self.account.pk]))
        self.assertIn(reverse('resident_appeal_new', args=[self.account.pk]), PageElements(response).links)

    def test_password_change_keeps_session_and_shows_single_success_receipt(self):
        new_password = 'Different-synthetic-resident-2026!'
        response = self.client.post(reverse('resident_password_change'), {
            'old_password': self.password, 'new_password1': new_password,
            'new_password2': new_password,
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.wsgi_request.user.is_authenticated)
        self.assertContains(response, 'Пароль изменён.', count=1)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))
        again = self.client.get(reverse('resident_dashboard'))
        self.assertNotContains(again, 'Пароль изменён.')

    def test_invalid_password_change_preserves_password_and_has_no_success_receipt(self):
        response = self.client.post(reverse('resident_password_change'), {
            'old_password': 'wrong password', 'new_password1': 'Different-synthetic-2026!',
            'new_password2': 'Different-synthetic-2026!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Пароль изменён.')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_water_success_receipt_is_rendered_once(self):
        response = self.client.post(self.reading_url, {
            'date': self.today.isoformat(), 'value': '11.234',
        }, follow=True)
        self.assertContains(response, f'Показание счётчика «{self.meter.serial}» передано на проверку.', count=1)
        self.assertEqual(ControllerReadingSubmission.objects.filter(submitted_by=self.user).count(), 1)

    def test_resident_home_does_not_display_unrelated_staff_session_message(self):
        session = self.client.session
        session['_messages'] = SessionStorage(self.client.get(self.water_url).wsgi_request).serialize_messages([
            Message(messages.SUCCESS, 'Synthetic staff-only receipt'),
        ])
        session.save()
        response = self.client.get(reverse('resident_dashboard'))
        self.assertNotContains(response, 'Synthetic staff-only receipt')
