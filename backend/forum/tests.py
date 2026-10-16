from datetime import timedelta
from unittest.mock import patch
import time

from allauth.account.models import EmailAddress
from django.contrib.auth import authenticate
from django.contrib.auth.models import Group, Permission
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from water.models import User
from .models import Post, Profile, Report, Section, Topic


@override_settings(FORUM_ENABLED=True, RESIDENT_EMAIL_LOGIN_ENABLED=True,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.resident = User.objects.create_user('old-resident', email='resident@example.test')
        self.login_url = reverse('account_login')
        self.confirm_url = reverse('account_confirm_login_code')

    def send_code(self, email='resident@example.test'):
        with patch('allauth.account.adapter.DefaultAccountAdapter.generate_login_code', return_value='123456'):
            response = self.client.post(self.login_url, {'email': email})
        self.assertRedirects(response, self.confirm_url)
        self.assertIn('123456', mail.outbox[-1].body)
        self.assertEqual(mail.outbox[-1].to, [email.lower()])
        return '123456'

    def test_existing_resident_code_verifies_email_and_is_single_use(self):
        code = self.send_code()
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.post(self.confirm_url, {'code': 'wrong'}).status_code, 200)
        response = self.client.post(self.confirm_url, {'code': code})
        self.assertRedirects(response, reverse('forum:index'))
        self.assertEqual(int(self.client.session['_auth_user_id']), self.resident.pk)
        self.assertTrue(EmailAddress.objects.get(user=self.resident).verified)
        self.assertEqual(len(mail.outbox), 1)
        self.client.post(reverse('resident_logout'))
        self.client.post(self.confirm_url, {'code': code})
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_code_session_cannot_become_staff_session_after_promotion(self):
        code = self.send_code()
        self.client.post(self.confirm_url, {'code': code})
        self.assertEqual(self.client.session['_auth_user_backend'], 'forum.auth.EmailCodeBackend')
        self.resident.is_staff = True
        self.resident.save()
        response = self.client.get('/work/')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin/login/', response.url)

    def test_changed_email_invalidates_pending_code(self):
        code = self.send_code()
        self.resident.email = 'changed@example.test'
        self.resident.save()
        self.assertEqual(self.client.post(self.confirm_url, {'code': code}).status_code, 403)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_login_code_expires(self):
        code = self.send_code()
        with patch('allauth.account.internal.flows.code_verification.time.time', return_value=time.time() + 181):
            self.assertEqual(self.client.post(self.confirm_url, {'code': code}).status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_three_bad_attempts_abort_code(self):
        code = self.send_code()
        for _ in range(3):
            self.client.post(self.confirm_url, {'code': 'wrong'})
        self.client.post(self.confirm_url, {'code': code})
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_signup_without_password_and_email_code(self):
        url = reverse('account_signup')
        response = self.client.get(url)
        self.assertEqual(set(response.context['form'].fields), {'email'})
        with patch('allauth.account.adapter.DefaultAccountAdapter.generate_email_verification_code', return_value='654321'):
            response = self.client.post(url, {'email': 'new@example.test'})
        self.assertRedirects(response, reverse('account_email_verification_sent'))
        user = User.objects.get(email='new@example.test')
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertIn('654321', mail.outbox[-1].body)
        response = self.client.post(reverse('account_email_verification_sent'), {'code': '654321', 'action': 'verify'})
        self.assertRedirects(response, reverse('forum:index'))
        self.assertEqual(int(self.client.session['_auth_user_id']), user.pk)
        self.assertTrue(EmailAddress.objects.get(user=user).verified)
        self.assertEqual(self.client.get(reverse('account_email_verification_sent'), follow=True).status_code, 200)
        self.assertEqual(self.client.get(reverse('forum:profile')).status_code, 200)
        self.assertEqual(self.client.get(reverse('resident_access_request')).status_code, 200)
        self.assertContains(self.client.get(reverse('resident_dashboard')), 'Запросить доступ', status_code=403)

    def test_signup_code_expires_and_does_not_log_in(self):
        with patch('allauth.account.adapter.DefaultAccountAdapter.generate_email_verification_code', return_value='654321'):
            self.client.post(reverse('account_signup'), {'email': 'new@example.test'})
        with patch('allauth.account.internal.flows.code_verification.time.time', return_value=time.time() + 601):
            self.client.post(reverse('account_email_verification_sent'), {'code': '654321'})
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertFalse(EmailAddress.objects.get(email='new@example.test').verified)

    def test_staff_code_and_signup_are_blocked_password_still_works(self):
        staff = User.objects.create_user('staff', email='staff@example.test', password='test-password', is_staff=True)
        for url in (self.login_url, reverse('account_signup')):
            self.assertContains(self.client.post(url, {'email': staff.email}), 'недоступен')
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.client.get('/admin/login/').status_code, 200)
        self.assertEqual(self.client.get('/admin/account/login/').status_code, 200)
        self.assertEqual(authenticate(request=self.client.request().wsgi_request, username='staff', password='test-password'), staff)
        # No new email/password fallback on the existing staff surfaces.
        self.assertIsNone(authenticate(request=self.client.request().wsgi_request, username=staff.email, password='test-password'))
        self.assertRedirects(self.client.post('/admin/login/', {
            'username': 'staff', 'password': 'test-password', 'next': '/work/',
        }), '/work/')
        self.assertEqual(self.client.get('/work/').status_code, 200)
        self.assertEqual(self.client.get(self.login_url).status_code, 403)

    def test_staff_promotion_while_code_pending_is_blocked(self):
        code = self.send_code()
        self.resident.is_staff = True
        self.resident.save()
        self.assertEqual(self.client.post(self.confirm_url, {'code': code}).status_code, 403)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_ambiguous_legacy_email_is_not_silently_assigned(self):
        User.objects.create_user('duplicate', email='RESIDENT@example.test')
        self.assertContains(self.client.post(self.login_url, {'email': self.resident.email}), 'недоступен')
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailAddress.objects.exists())

    def test_existing_email_signup_never_creates_duplicate_user(self):
        response = self.client.post(reverse('account_signup'), {'email': self.resident.email})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(User.objects.filter(email__iexact=self.resident.email).count(), 1)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_inactive_user_cannot_enter(self):
        self.resident.is_active = False
        self.resident.save()
        self.client.post(self.login_url, {'email': self.resident.email})
        self.client.post(self.confirm_url, {'code': '123456'})
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_external_next_is_rejected(self):
        code = self.send_code()
        response = self.client.post(self.confirm_url + '?next=https://outside.example/', {'code': code})
        self.assertRedirects(response, reverse('forum:index'))

    def test_email_flag_blocks_all_new_auth_paths_but_keeps_legacy_login(self):
        with override_settings(RESIDENT_EMAIL_LOGIN_ENABLED=False):
            for name in ('account_login', 'account_signup', 'account_confirm_login_code', 'account_email_verification_sent'):
                self.assertEqual(self.client.get(reverse(name)).status_code, 404)
            self.assertEqual(self.client.get(reverse('resident_login')).status_code, 200)

    def test_auth_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(self.login_url, {'email': self.resident.email}).status_code, 403)
        self.assertEqual(client.post(reverse('account_signup'), {'email': 'new@example.test'}).status_code, 403)


@override_settings(FORUM_ENABLED=True, FORUM_PUBLIC_READ=False, RESIDENT_EMAIL_LOGIN_ENABLED=True)
class ForumTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = User.objects.create_user('private-username', email='private@example.test', first_name='СекретноеФИО')
        cls.other = User.objects.create_user('other', email='other@example.test')
        for user in (cls.author, cls.other):
            EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
        cls.section = Section.objects.create(name='Соседи', description='Жизнь ТСН')
        cls.topic = Topic.objects.create(section=cls.section, title='Вопрос соседям', body='Начальный текст', author=cls.author)
        cls.post = Post.objects.create(topic=cls.topic, body='Первый ответ', author=cls.other)
        call_command('setup_roles', verbosity=0)
        cls.moderator = User.objects.create_user('moderator', is_staff=True)
        cls.moderator.groups.add(Group.objects.get(name='Администратор ТСН'))

    def setUp(self):
        cache.clear()
        self.url = reverse('forum:topic', args=[self.topic.pk])
        self.new_url = reverse('forum:new_topic', args=[self.section.pk])
        self.client.force_login(self.author, backend='django.contrib.auth.backends.ModelBackend')

    def mod(self, action, reason='', post=False):
        url = reverse('forum:moderate_post' if post else 'forum:moderate_topic', args=[self.post.pk if post else self.topic.pk])
        return self.client.post(url, {'action': action, 'reason': reason})

    def test_read_requires_login_unless_public_enabled(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        with override_settings(FORUM_PUBLIC_READ=True):
            self.assertContains(self.client.get(self.url), self.topic.title)
            self.assertEqual(self.client.post(self.url, {'body': 'Без входа'}).status_code, 403)
            self.assertEqual(self.client.get(self.new_url).status_code, 403)

    def test_verified_email_suffices_without_account_grant(self):
        response = self.client.post(self.new_url, {'title': 'Новая тема', 'body': 'Без привязки'})
        self.assertEqual(response.status_code, 302)
        item = Topic.objects.get(title='Новая тема')
        self.assertEqual(item.author, self.author)
        self.assertEqual(item.history.get().history_user, self.author)
        self.assertEqual(self.client.post(self.url, {'body': 'Мой ответ'}).status_code, 302)
        self.assertEqual(Post.objects.get(body='Мой ответ').history.get().history_user, self.author)

    def test_unverified_cannot_write_edit_or_report(self):
        user = User.objects.create_user('unverified', email='unverified@example.test')
        self.client.force_login(user, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.url, {'body': 'Текст'}).status_code, 403)
        self.assertEqual(self.client.post(self.new_url, {'title': 'Тема', 'body': 'Текст'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('forum:report_topic', args=[self.topic.pk]), {'reason': 'Спам'}).status_code, 403)

    def test_hidden_topic_and_reply_are_invisible_even_to_author(self):
        self.client.force_login(self.moderator, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.mod('hide', 'Спам', post=True).status_code, 302)
        self.client.force_login(self.author, backend='django.contrib.auth.backends.ModelBackend')
        self.assertNotContains(self.client.get(self.url), self.post.body)
        self.client.force_login(self.moderator, backend='django.contrib.auth.backends.ModelBackend')
        self.assertContains(self.client.get(self.url), 'Спам')
        self.assertEqual(self.mod('hide', 'Спам темы').status_code, 302)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.history.first().history_user, self.moderator)
        self.assertEqual(self.topic.history.first().history_change_reason, 'Скрытие: Спам темы')
        self.client.force_login(self.author, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertNotContains(self.client.get(reverse('forum:section', args=[self.section.pk])), self.topic.title)
        self.assertEqual(self.client.get(reverse('forum:edit_topic', args=[self.topic.pk])).status_code, 403)
        self.client.logout()
        with override_settings(FORUM_PUBLIC_READ=True):
            self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_moderation_requires_permission_and_reason(self):
        self.assertEqual(self.mod('pin').status_code, 403)
        self.client.force_login(self.moderator, backend='django.contrib.auth.backends.ModelBackend')
        self.assertTrue(self.moderator.has_perm('forum.moderate_forum'))
        self.assertEqual(self.mod('hide').status_code, 400)
        for action in ('pin', 'close'):
            self.assertEqual(self.mod(action).status_code, 302)
        self.topic.refresh_from_db()
        self.assertTrue(self.topic.pinned)
        self.assertTrue(self.topic.closed)
        self.assertEqual(self.mod('hide', 'Причина').status_code, 302)
        self.assertEqual(self.mod('show').status_code, 302)
        self.topic.refresh_from_db()
        self.assertFalse(self.topic.hidden)
        self.assertEqual(self.topic.hidden_reason, 'Причина')

    def test_staff_without_moderation_permission_is_denied(self):
        staff = User.objects.create_user('just-staff', is_staff=True)
        self.client.force_login(staff, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.mod('pin').status_code, 403)
        self.assertEqual(self.client.get(reverse('forum_moderation')).status_code, 403)

    def test_explicit_moderation_permission_is_supported(self):
        self.author.user_permissions.add(Permission.objects.get(content_type__app_label='forum', codename='moderate_forum'))
        self.assertEqual(self.mod('pin').status_code, 302)

    def test_closed_topic_blocks_reply_and_edits(self):
        self.client.force_login(self.moderator, backend='django.contrib.auth.backends.ModelBackend')
        self.mod('close')
        self.client.force_login(self.author, backend='django.contrib.auth.backends.ModelBackend')
        self.assertEqual(self.client.post(self.url, {'body': 'Ответ'}).status_code, 403)
        self.assertEqual(self.client.post(reverse('forum:edit_topic', args=[self.topic.pk]), {'title': 'Правка', 'body': 'Правка'}).status_code, 403)

    def test_author_can_edit_only_own_for_15_minutes_with_history(self):
        edit_url = reverse('forum:edit_topic', args=[self.topic.pk])
        self.assertEqual(self.client.post(edit_url, {'title': 'Правка', 'body': 'Новый текст'}).status_code, 302)
        self.topic.refresh_from_db()
        self.assertEqual(self.topic.history.count(), 2)
        self.assertIsNotNone(self.topic.edited_at)
        self.assertEqual(self.topic.author, self.author)
        self.assertEqual(self.client.get(reverse('forum:edit_post', args=[self.post.pk])).status_code, 403)
        self.topic.created_at = timezone.now() - timedelta(minutes=15)
        self.topic.save()
        self.assertEqual(self.client.get(edit_url).status_code, 403)

    def test_reply_edit_within_window_and_foreign_ids_ignored(self):
        self.client.force_login(self.other, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.post(reverse('forum:edit_post', args=[self.post.pk]), {
            'body': 'Исправленный ответ', 'author': self.author.pk, 'hidden': True,
        })
        self.assertEqual(response.status_code, 302)
        self.post.refresh_from_db()
        self.assertEqual(self.post.author, self.other)
        self.assertFalse(self.post.hidden)
        self.assertEqual(self.post.history.count(), 2)

    def test_shared_rate_limit_counts_topics_and_replies(self):
        with override_settings(FORUM_MESSAGES_PER_MINUTE=2):
            self.assertEqual(self.client.post(self.url, {'body': 'Второе сообщение'}).status_code, 302)
            self.assertEqual(self.client.post(self.new_url, {'title': 'Третье', 'body': 'Текст'}).status_code, 429)
            self.assertEqual(self.client.post(self.url, {'body': 'Третье'}).status_code, 429)
        self.assertFalse(Post.objects.filter(body='Третье').exists())

    def test_length_limits_and_blank_text(self):
        for text in ('x' * 5001, '   '):
            self.assertEqual(self.client.post(self.url, {'body': text}).status_code, 200)
        self.assertEqual(Post.objects.count(), 1)
        self.assertEqual(self.client.post(self.new_url, {'title': 'x' * 161, 'body': 'Текст'}).status_code, 200)
        self.assertEqual(Topic.objects.count(), 1)

    def test_html_is_escaped_line_breaks_kept_and_identity_private(self):
        self.topic.body = '<script>alert(1)</script>\nСледующая строка'
        self.topic.save()
        Profile.objects.create(user=self.author, nickname='<img src=x onerror=alert(1)>')
        response = self.client.get(self.url)
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;<br>Следующая строка')
        self.assertNotContains(response, '<script>')
        self.assertNotContains(response, '<img src=x')
        self.assertNotContains(response, self.author.username)
        self.assertNotContains(response, self.author.email)
        self.assertNotContains(response, self.author.first_name)

    def test_report_enters_workspace_queue_and_can_be_resolved(self):
        url = reverse('forum:report_post', args=[self.post.pk])
        self.assertEqual(self.client.post(url, {'reason': 'Оскорбление'}).status_code, 302)
        self.client.post(url, {'reason': 'Повтор'})
        self.assertEqual(Report.objects.count(), 1)
        report = Report.objects.get()
        self.assertEqual(report.post, self.post)
        self.assertEqual(report.reporter, self.author)
        self.assertEqual(self.client.get(reverse('forum_moderation')).status_code, 302)
        self.client.force_login(self.moderator, backend='django.contrib.auth.backends.ModelBackend')
        self.assertContains(self.client.get(reverse('forum_moderation')), 'Оскорбление')
        self.client.post(reverse('forum_moderation'), {'report_id': report.pk})
        report.refresh_from_db()
        self.assertEqual(report.resolved_by, self.moderator)
        self.assertIsNotNone(report.resolved_at)
        self.assertNotContains(self.client.get(reverse('forum_moderation')), 'Оскорбление')

    def test_sections_are_managed_by_board_in_admin(self):
        self.assertEqual(self.client.get(reverse('admin:forum_section_add')).status_code, 302)
        self.client.force_login(self.moderator, backend='django.contrib.auth.backends.ModelBackend')
        response = self.client.post(reverse('admin:forum_section_add'), {'name': 'Благоустройство', 'description': 'Двор'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Section.objects.filter(name='Благоустройство').exists())
        with override_settings(FORUM_ENABLED=False):
            self.assertEqual(self.client.get(reverse('admin:forum_section_add')).status_code, 403)

    def test_nickname_is_opt_in_and_never_replaces_registry_identity(self):
        self.client.post(reverse('forum:profile'), {'nickname': 'Сосед'})
        self.assertContains(self.client.get(self.url), 'Сосед')
        self.author.refresh_from_db()
        self.assertEqual(self.author.first_name, 'СекретноеФИО')
        self.assertEqual(self.author.username, 'private-username')

    def test_feature_off_hides_endpoints_and_links(self):
        with override_settings(FORUM_ENABLED=False):
            for url in (self.url, self.new_url, reverse('forum:index'), reverse('forum:profile')):
                self.assertEqual(self.client.get(url).status_code, 404)
            self.assertIsNone(self.client.get('/admin/public/content/').json()['forum_url'])
            self.assertNotContains(self.client.get(reverse('resident_dashboard')), 'Обсуждения', status_code=403)
            self.client.force_login(self.moderator, backend='django.contrib.auth.backends.ModelBackend')
            self.assertEqual(self.client.get(reverse('forum_moderation')).status_code, 404)

    def test_csrf_and_post_only_mutations(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.author, backend='django.contrib.auth.backends.ModelBackend')
        urls = [self.url, self.new_url, reverse('forum:edit_topic', args=[self.topic.pk]),
                reverse('forum:report_topic', args=[self.topic.pk]), reverse('forum:profile')]
        for url in urls:
            self.assertEqual(client.post(url, {'body': 'Ответ'}).status_code, 403)
        mod_url = reverse('forum:moderate_topic', args=[self.topic.pk])
        self.assertEqual(client.get(mod_url).status_code, 405)
        self.assertEqual(client.post(mod_url, {'action': 'hide', 'reason': 'Причина'}).status_code, 403)

    def test_mobile_markup_and_public_navigation(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'name="viewport" content="width=device-width,initial-scale=1"')
        self.assertContains(response, 'forum/forum.css')
        self.assertEqual(self.client.get('/admin/public/content/').json()['forum_url'], reverse('forum:index'))
        self.assertNotContains(self.client.get(reverse('resident_dashboard')), 'Обсуждения', status_code=403)
