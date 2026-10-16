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
