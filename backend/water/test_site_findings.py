"""Regressions from the offline site audit (synthetic data only)."""
from datetime import timedelta

from django.contrib import admin
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase
from django.utils import timezone

from .models import Account, Person, User
from .board_polls import (
    BoardAuditEvent, BoardDiscussionComment, BoardMembership, BoardPoll, BoardQuestion, BoardVote,
)


class SiteFindingTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username='audit-staff', is_staff=True)
        self.admin = User.objects.create_superuser(username='audit-admin', email='audit@example.test')
        self.account = Account.objects.create(number='AUDIT-1', plot='Синтетический участок')

    def grant(self, user, *codenames):
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='water', codename__in=codenames,
        ))
        return User.objects.get(pk=user.pk)

    def poll(self):
        return BoardPoll.objects.create(
            title='Синтетический опрос', created_by=self.admin,
            opens_at=timezone.now() - timedelta(days=2),
            closes_at=timezone.now() + timedelta(days=1),
        )

    def test_board_admin_read_requires_model_permission(self):
        poll = self.poll()
        question = BoardQuestion.objects.create(poll=poll, order=1, text='Синтетический вопрос')
        BoardMembership.objects.create(user=self.staff, starts=timezone.localdate() - timedelta(days=3))
        vote = BoardVote.objects.create(question=question, user=self.staff, choice='for')
        comment = BoardDiscussionComment.objects.create(question=question, author=self.staff, body='Закрыто')
        audit = BoardAuditEvent.objects.first()
        for obj in (vote, comment, audit):
            model = obj._meta.model_name
            with self.subTest(model=model):
                self.client.force_login(self.staff)
                for path in (f'/admin/water/{model}/', f'/admin/water/{model}/{obj.pk}/change/'):
                    self.assertEqual(self.client.get(path).status_code, 403)
                viewer = self.grant(self.staff, f'view_{model}')
                self.client.force_login(viewer)
                self.assertEqual(self.client.get(f'/admin/water/{model}/').status_code, 200)
                self.assertEqual(self.client.get(f'/admin/water/{model}/{obj.pk}/change/').status_code, 200)
                for method in ('get', 'head', 'options', 'post'):
                    request = getattr(RequestFactory(), method)('/')
                    request.user = viewer
                    self.assertFalse(admin.site._registry[type(obj)].has_change_permission(request, obj))
                self.assertEqual(self.client.post(f'/admin/water/{model}/{obj.pk}/change/', {}).status_code, 403)

    def test_grant_history_requires_card_privacy_and_history_permissions(self):
        from .portal_permissions import PortalGrant
        person = Person.objects.create(full_name='Синтетическое закрытое ФИО')
        grant = PortalGrant.objects.create(
            person=person, account=self.account, starts=timezone.localdate(),
            basis='Закрытое основание', verified_by=self.admin,
        )
        history_id = grant.history.first().pk
        paths = (
            f'/admin/water/portalgrant/{grant.pk}/history/',
            f'/admin/water/portalgrant/{grant.pk}/history/{history_id}/',
        )
        for history_perm in ('view_historicalportalgrant', 'change_historicalportalgrant'):
            self.staff.user_permissions.clear()
            self.staff = self.grant(self.staff, history_perm, 'view_portalgrant')
            self.client.force_login(self.staff)
            for path in paths:
                with self.subTest(permission=history_perm, path=path):
                    self.assertEqual(self.client.get(path).status_code, 403)
        self.staff = self.grant(self.staff, 'access_private_registry', 'view_historicalportalgrant')
        self.client.force_login(self.staff)
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, 200)
        self.staff.user_permissions.clear()
        self.staff = self.grant(self.staff, 'access_private_registry', 'view_portalgrant')
        self.client.force_login(self.staff)
        for path in paths:
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_appeal_search_accepts_unicode_without_integer_errors(self):
        self.client.force_login(self.admin)
        for param in ('q', 'account'):
            for value in ('²', '１２３', '123', 'текст'):
                with self.subTest(param=param, value=value):
                    self.assertEqual(self.client.get('/work/appeals/', {param: value}).status_code, 200)

    def test_account_reading_journal_link_filters_only_this_account(self):
        import re
        from .models import Meter, Reading, SupplyNode
        node = SupplyNode.objects.create(name='Синтетический узел')
        other = Account.objects.create(number='AUDIT-2')
        for account in (self.account, other):
            meter = Meter.objects.create(serial=f'AUDIT-{account.pk}', kind='individual', account=account, node=node)
            Reading.objects.create(meter=meter, date=timezone.localdate(), value='1')
        self.client.force_login(self.admin)
        page = self.client.get(f'/work/accounts/{self.account.pk}/')
        link = re.search(r'href="([^"]+)">Журнал показаний', page.content.decode())[1]
        journal = self.client.get(link)
        self.assertEqual(journal.status_code, 200)
        self.assertEqual(list(journal.context['cl'].queryset.values_list('meter__account_id', flat=True)), [self.account.pk])
        self.assertEqual(self.client.get('/admin/water/reading/', {'meter__account__phone__icontains': '1'}).status_code, 400)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(link).status_code, 403)

    def test_staff_protocol_link_uses_staff_permissions(self):
        import re
        from tempfile import TemporaryDirectory
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .board_polls import BoardProtocol
        poll = self.poll()
        poll.closed_at = timezone.now()
        poll.save()
        with TemporaryDirectory() as media, self.settings(MEDIA_ROOT=media):
            BoardProtocol.objects.create(
                poll=poll, document=SimpleUploadedFile('protocol.pdf', b'%PDF-1.4\nsynthetic'),
                uploaded_by=self.admin,
            )
            viewer = self.grant(self.staff, 'view_boardpoll')
            self.client.force_login(viewer)
            detail = self.client.get(f'/work/governance/{poll.pk}/')
            link = re.search(r'href="([^"]+)">Скачать протокол', detail.content.decode())[1]
            response = self.client.get(link)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4\nsynthetic')
            self.assertIn('private', response['Cache-Control'])
            self.assertIn('no-store', response['Cache-Control'])
            self.assertEqual(self.client.get(f'/admin/cabinet/board/poll/{poll.pk}/protocol/').status_code, 403)
            self.staff.user_permissions.clear()
            self.client.force_login(User.objects.get(pk=self.staff.pk))
            self.assertEqual(self.client.get(link).status_code, 403)
            self.client.logout()
            self.assertEqual(self.client.get(link).status_code, 302)

    def test_controller_empty_line_is_authorized_empty_scope(self):
        from django.core.exceptions import PermissionDenied
        from .controller_scope import ControllerLineAccess
        from .models import SupplyNode, WaterGroup
        from .staff_workspace import scoped_accounts
        node = SupplyNode.objects.create(name='Пустой узел')
        group = WaterGroup.objects.create(name='Пустая линия', node=node)
        self.staff = self.grant(self.staff, 'use_controller_workspace')
        ControllerLineAccess.objects.create(user=self.staff, group=group, starts=timezone.localdate())
        self.client.force_login(self.staff)
        for path in ('/work/', '/work/accounts/', '/work/search/?q=AUDIT'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'Синтетический участок')
        self.assertFalse(scoped_accounts(self.staff).exists())
        self.assertEqual(self.client.get(f'/work/accounts/{self.account.pk}/').status_code, 404)
        outsider = User.objects.create_user(username='no-scope', is_staff=True)
        with self.assertRaises(PermissionDenied):
            scoped_accounts(outsider)
        self.client.force_login(outsider)
        self.assertEqual(self.client.get('/work/accounts/').status_code, 403)
