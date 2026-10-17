from datetime import timedelta
from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from water.access_policy import CAPABILITIES, ROLE_TEMPLATES
from water.models import User
from water._test_auth import login_with_admin_otp
from .models import HomepageAnnouncement, SiteContacts, PublicImage, PublicNews
from .publication_workflow import apply_publication_state


class HomepageEditorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command('setup_roles', stdout=StringIO())
        cls.actor = User.objects.create_user(username='site-admin', is_staff=True)
        cls.actor.groups.add(Group.objects.get(name='Администратор ТСН'))
        cls.viewer = User.objects.create_user(username='site-viewer', is_staff=True)
        cls.viewer.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='public_site',
            codename__in=['view_homepageannouncement', 'view_sitecontacts', 'view_publicnews'],
        ))
        cls.denied = User.objects.create_user(username='site-denied', is_staff=True)

    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        context = override_settings(MEDIA_ROOT=directory.name)
        context.enable()
        self.addCleanup(context.disable)
        self.today = timezone.localdate()

    def announcement(self, **kwargs):
        return HomepageAnnouncement.objects.create(**{'title': 'Объявление', **kwargs})

    def feed(self):
        return self.client.get(reverse('public_content')).json()

    def publish(self, item):
        apply_publication_state(item, actor=self.actor, is_published=True, confirmed=True)
        item.save()

    def payload(self, **kwargs):
        return {'title': 'Новое объявление', 'label': 'Важно', 'starts_on': str(self.today),
                'version': 0, **kwargs}

    def test_empty_feed_keeps_backwards_compatibility(self):
        payload = self.feed()
        self.assertIsNone(payload['announcement'])
        self.assertIsNone(payload['contacts'])
        self.assertEqual(payload['news'], [])
        self.assertEqual(payload['documents'], [])

    def test_announcement_gate_and_inclusive_date_boundaries(self):
        draft = self.announcement()
        unchecked = self.announcement(is_published=True)
        future = self.announcement(starts_on=self.today + timedelta(days=1))
        self.publish(future)
        expired = self.announcement(starts_on=self.today - timedelta(days=2), ends_on=self.today - timedelta(days=1))
        self.publish(expired)
        self.assertIsNone(self.feed()['announcement'])
        visible = self.announcement(title='Показать сегодня', ends_on=self.today)
        self.publish(visible)
        self.assertEqual(self.feed()['announcement']['title'], visible.title)
        for item in (draft, unchecked, future, expired):
            self.assertEqual(self.client.get(reverse('public_announcement_image', args=[item.pk])).status_code, 404)

    def test_only_one_announcement_latest_start_then_id_wins(self):
        old = self.announcement(title='Старое', starts_on=self.today - timedelta(days=1))
        self.publish(old)
        current = self.announcement(title='Новое')
        self.publish(current)
        replacement = self.announcement(title='Замена')
        self.publish(replacement)
        self.assertEqual(self.feed()['announcement']['title'], replacement.title)
        replacement.is_published = False
        replacement.save()
        self.assertEqual(self.feed()['announcement']['title'], current.title)

    def test_invalid_period_and_button_targets_are_rejected(self):
        for kwargs in [dict(ends_on=self.today - timedelta(days=1)), dict(button_text='Читать'),
                       dict(button_url='https://example.com/'),
                       dict(button_text='Открыть', button_url='javascript:alert(1)')]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                self.announcement(**kwargs)

    def test_contacts_are_singleton_and_explicitly_published(self):
        item = SiteContacts.objects.create(email='board@example.com', office_hours='По записи')
        self.assertIsNone(self.feed()['contacts'])
        with self.assertRaises(ValidationError):
            SiteContacts.objects.create(phone='Синтетический телефон')
        self.publish(item)
        self.assertEqual(self.feed()['contacts']['office_hours'], 'По записи')
        self.assertNotIn('published_by', self.feed()['contacts'])
        item.is_published = False
        item.save()
        self.assertIsNone(self.feed()['contacts'])

    def test_history_and_optimistic_lock(self):
        item = self.announcement()
        stale = HomepageAnnouncement.objects.get(pk=item.pk)
        item.title = 'Изменённый заголовок'
        item._history_user = self.actor
        item._change_reason = 'Проверка истории'
        item.save()
        self.assertEqual(item.history.count(), 2)
        self.assertEqual(item.history.first().history_user, self.actor)
        self.assertEqual(item.history.first().history_change_reason, 'Проверка истории')
        with self.assertRaises(ValidationError):
            stale.save()
        self.assertEqual(SiteContacts.objects.create().history.count(), 1)

    def test_news_pins_sort_before_newer_and_scheduled_stays_hidden(self):
        normal = PublicNews.objects.create(title='Новее', summary='Анонс', body='Текст', is_published=True, public_checked=True)
        pinned = PublicNews.objects.create(title='Закреплено', summary='Анонс', body='Текст',
                                          published_on=self.today - timedelta(days=2),
                                          is_featured=True, is_published=True, public_checked=True)
        future = PublicNews.objects.create(title='Позже', summary='Анонс', body='Текст',
                                          published_on=self.today + timedelta(days=1),
                                          is_featured=True, is_published=True, public_checked=True)
        self.assertEqual(future.site_status, 'Запланировано')
        self.assertEqual([item['id'] for item in self.feed()['news']], [pinned.pk, normal.pk])

    def test_permission_denied_and_viewers_cannot_post(self):
        item = self.announcement()
        contacts = SiteContacts.objects.create()
        news = PublicNews.objects.create(title='Черновик', summary='Анонс', body='Скрытый текст')
        urls = [reverse('staff_workspace:site_block', args=['announcement', item.pk]),
                reverse('staff_workspace:site_block', args=['contacts', contacts.pk])]
        previews = [reverse('staff_workspace:site_preview', args=[kind, obj.pk])
                    for kind, obj in [('news', news), ('announcement', item), ('contacts', contacts)]]
        self.client.force_login(self.denied)
        for url in urls + previews:
            self.assertEqual(self.client.get(url).status_code, 403)
        for url in urls:
            self.assertEqual(self.client.post(url, {}).status_code, 403)
        self.client.force_login(self.viewer)
        for url in urls + previews:
            self.assertEqual(self.client.get(url).status_code, 200)
        for url in urls:
            self.assertEqual(self.client.post(url, {}).status_code, 403)
        for url in previews:
            self.assertEqual(self.client.post(url, {}).status_code, 405)

    def test_preview_is_private_escaped_and_never_publishes(self):
        item = self.announcement(title='<script>private draft</script>')
        news = PublicNews.objects.create(title='Черновик', summary='Анонс', body='</script><script>private</script>')
        login_with_admin_otp(self.client, self.actor)
        for kind, record in [('announcement', item), ('news', news)]:
            before = record.updated_at
            response = self.client.get(reverse('staff_workspace:site_preview', args=[kind, record.pk]))
            self.assertEqual(response.status_code, 200)
            self.assertIn('private', response['Cache-Control'])
            self.assertIn('no-store', response['Cache-Control'])
            self.assertContains(response, 'public-preview-data')
            self.assertContains(response, '/app.js?v=site-editor')
            self.assertNotContains(response, '<script>private')
            record.refresh_from_db()
            self.assertFalse(record.is_published)
            self.assertEqual(record.updated_at, before)
        self.assertEqual(item.history.count(), 1)
        self.assertIsNone(self.feed()['announcement'])
        self.assertEqual(self.feed()['news'], [])

    def test_staff_publication_confirmation_and_history_actor(self):
        login_with_admin_otp(self.client, self.actor)
        url = reverse('staff_workspace:site_block_create', args=['announcement'])
        data = self.payload(is_published='on')
        rejected = self.client.post(url, data)
        self.assertContains(rejected, 'Перед публикацией проверьте материал')
        self.assertEqual(HomepageAnnouncement.objects.count(), 0)
        response = self.client.post(url, {**data, 'confirm_publication': 'on'})
        self.assertEqual(response.status_code, 302)
        item = HomepageAnnouncement.objects.get()
        self.assertTrue(item.public_checked)
        self.assertEqual(item.published_by, self.actor)
        self.assertEqual(item.history.first().history_user, self.actor)
        self.assertEqual(self.feed()['announcement']['title'], item.title)

    def test_contacts_edit_requires_reason_and_single_work_record(self):
        login_with_admin_otp(self.client, self.actor)
        url = reverse('staff_workspace:site_block_create', args=['contacts'])
        data = {'email': 'board@example.com', 'version': 0}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        item = SiteContacts.objects.get()
        data['version'] = item.version
        self.assertContains(self.client.post(url, data), 'укажите причину')
        response = self.client.post(url, {**data, 'change_reason': 'Уточнение приёма',
                                         'office_hours': 'По записи', 'is_published': 'on',
                                         'confirm_publication': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SiteContacts.objects.count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.history.count(), 2)
        self.assertEqual(item.history.first().history_change_reason, 'Уточнение приёма')

    def test_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        login_with_admin_otp(client, self.actor)
        self.assertEqual(client.post(reverse('staff_workspace:site_block_create', args=['announcement']), self.payload()).status_code, 403)

    def test_images_only_public_for_selected_active_announcement(self):
        image = PublicImage.objects.create(title='Картинка', image=SimpleUploadedFile('sample.png', b'\x89PNG\r\n\x1a\nsynthetic'))
        item = self.announcement(image=image)
        url = reverse('public_announcement_image', args=[item.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.viewer)
        preview = self.client.get(reverse('staff_workspace:site_preview_image', args=[item.pk]))
        self.assertEqual(preview.status_code, 200)
        # Consume the client wrapper so request_finished cannot close the
        # connection inside TestCase's transaction. Also verify the actual file.
        self.assertEqual(b''.join(preview.streaming_content), b'\x89PNG\r\n\x1a\nsynthetic')
        self.assertTrue(preview.closed)
        self.publish(item)
        response = self.client.get(url)
        self.assertEqual(response['Content-Type'], 'image/png')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(b''.join(response.streaming_content), b'\x89PNG\r\n\x1a\nsynthetic')
        self.assertTrue(response.closed)
        self.publish(self.announcement())
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertNotIn('/media/', str(self.feed()))

    def test_upload_and_selection(self):
        login_with_admin_otp(self.client, self.actor)
        response = self.client.post(reverse('staff_workspace:site_block_create', args=['announcement']),
                                    self.payload(image_upload=SimpleUploadedFile('sample.png', b'\x89PNG\r\n\x1a\nsynthetic')))
        self.assertEqual(response.status_code, 302)
        item = HomepageAnnouncement.objects.get()
        self.assertIsNotNone(item.image_id)
        self.assertEqual(item.image.history.first().history_user, self.actor)
        response = self.client.get(reverse('staff_workspace:site_block', args=['announcement', item.pk]))
        self.assertContains(response, str(item.image))

    def test_svg_and_disguised_html_are_rejected(self):
        for name, data in [('unsafe.svg', b'<svg/>'), ('fake.png', b'<html>bad</html>'),
                           ('fake.jpg', b'\x89PNG\r\n\x1a\n')]:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                PublicImage.objects.create(title='Картинка', image=SimpleUploadedFile(name, data))

    def test_new_capabilities_keep_editor_publisher_separation(self):
        for domain in ('announcement', 'contacts'):
            editor = ROLE_TEMPLATES['public_content_editor'].capabilities
            publisher = ROLE_TEMPLATES['public_content_publisher'].capabilities
            self.assertIn(f'site.{domain}.edit', editor)
            self.assertNotIn(f'site.{domain}.publish', editor)
            self.assertIn(f'site.{domain}.publish', publisher)
            self.assertNotIn(f'site.{domain}.edit', publisher)
            self.assertTrue(CAPABILITIES[f'site.{domain}.publish'].require_mfa)
            self.assertTrue(CAPABILITIES[f'site.{domain}.publish'].maker_checker)
        self.assertFalse(self.denied.has_perm('public_site.change_homepageannouncement'))
        self.assertTrue(self.actor.has_perm('public_site.change_homepageannouncement'))

    def test_publisher_can_only_publish_saved_content(self):
        item = self.announcement()
        self.client.force_login(self.viewer)
        allowed = {'site.announcement.view', 'site.announcement.publish'}
        with patch('water.staff_site._can', side_effect=lambda user, cap: cap in allowed):
            response = self.client.post(reverse('staff_workspace:site_block', args=['announcement', item.pk]),
                                        self.payload(title='Подмена текста', version=item.version,
                                                     is_published='on', confirm_publication='on', change_reason='Публикация'))
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.title, 'Объявление')
        self.assertTrue(item.is_published)

    def test_deep_link_feed_can_load_old_news_and_hides_drafts(self):
        old = PublicNews.objects.create(title='Архивная новость', summary='Анонс', body='Полный текст',
                                        published_on=self.today - timedelta(days=30),
                                        is_published=True, public_checked=True)
        for index in range(13):
            PublicNews.objects.create(title=f'Свежая {index}', summary='Анонс', body='Текст',
                                      is_published=True, public_checked=True)
        self.assertNotIn(old.pk, [item['id'] for item in self.feed()['news']])
        url = reverse('public_content')
        self.assertEqual(self.client.get(url, {'news': old.pk}).json()['article']['id'], old.pk)
        old.is_published = False
        old.save()
        self.assertIsNone(self.client.get(url, {'news': old.pk}).json()['article'])
        for invalid in ('-1', 'x', '9' * 100, '１'):
            self.assertIsNone(self.client.get(url, {'news': invalid}).json()['article'])

    def test_linked_document_button_disappears_when_document_is_withdrawn(self):
        from .models import PublicDocument, PublicDocumentCategory
        category = PublicDocumentCategory.objects.create(name='Открытые документы')
        doc = PublicDocument.objects.create(category=category, title='Документ',
                                             document=SimpleUploadedFile('public.txt', b'public'),
                                             is_published=True, public_checked=True)
        item = self.announcement(document=doc, button_text='Читать документ')
        self.publish(item)
        self.assertEqual(self.feed()['announcement']['button_url'], reverse('public_document_download', args=[doc.pk]))
        doc.is_published = False
        doc.save()
        self.assertEqual(self.feed()['announcement']['button_url'], '')
        self.assertEqual(self.feed()['announcement']['button_text'], '')

    def test_new_admin_forms_and_singleton_are_available_with_permissions(self):
        login_with_admin_otp(self.client, self.actor)
        for model in ('homepageannouncement', 'sitecontacts'):
            self.assertEqual(self.client.get(f'/admin/public_site/{model}/add/').status_code, 200)
        contacts = SiteContacts.objects.create()
        self.assertEqual(self.client.get('/admin/public_site/sitecontacts/add/').status_code, 403)
        self.assertEqual(self.client.get(f'/admin/public_site/sitecontacts/{contacts.pk}/change/').status_code, 200)
        self.assertEqual(self.client.get(f'/admin/public_site/sitecontacts/{contacts.pk}/history/').status_code, 200)


    def test_editor_get_uses_iso_dates_and_does_not_change_data(self):
        item = self.announcement()
        login_with_admin_otp(self.client, self.actor)
        response = self.client.get(reverse('staff_workspace:site_block', args=['announcement', item.pk]))
        self.assertContains(response, f'value="{self.today.isoformat()}"')
        self.assertEqual(item.history.count(), 1)

    def test_editor_cannot_publish_by_forged_post(self):
        self.client.force_login(self.viewer)
        allowed = {'site.announcement.view', 'site.announcement.create', 'site.announcement.edit'}
        with patch('water.staff_site._can', side_effect=lambda user, cap: cap in allowed):
            response = self.client.post(reverse('staff_workspace:site_block_create', args=['announcement']),
                                        self.payload(is_published='on', confirm_publication='on'))
        self.assertEqual(response.status_code, 302)
        item = HomepageAnnouncement.objects.get()
        self.assertFalse(item.is_published)
        self.assertFalse(item.public_checked)
        self.assertIsNone(self.feed()['announcement'])


    def test_v2_roles_enforce_real_workspace_permissions(self):
        from water.access_control import assignment_from_role
        from water.access_policy import ScopeType
        from water.models import Person
        from water.resident_models import ResidentIdentity
        person = Person.objects.create(full_name='Синтетический редактор сайта')
        user = User.objects.create_user(username='site-v2-editor', is_staff=True)
        ResidentIdentity.objects.create(user=user, person=person, verified_by=self.actor, basis='Тест')
        assignment_from_role(person=person, role_code='public_content_editor', scope_type=ScopeType.ALL,
                             starts=self.today, basis='Тест редактора', granted_by=self.actor)
        self.client.force_login(user)
        response = self.client.post(reverse('staff_workspace:site_block_create', args=['announcement']),
                                    self.payload(is_published='on', confirm_publication='on'))
        self.assertEqual(response.status_code, 302)
        item = HomepageAnnouncement.objects.get()
        self.assertFalse(item.is_published)
        self.assertEqual(self.client.get(reverse('staff_workspace:site_preview', args=['announcement', item.pk])).status_code, 200)


    def test_publisher_cannot_approve_a_stale_reviewed_version(self):
        item = self.announcement()
        reviewed_version = item.version
        item.title = 'Обновлено после предпросмотра'
        item.save()
        self.client.force_login(self.viewer)
        allowed = {'site.announcement.view', 'site.announcement.publish'}
        with patch('water.staff_site._can', side_effect=lambda user, cap: cap in allowed):
            response = self.client.post(reverse('staff_workspace:site_block', args=['announcement', item.pk]),
                                        self.payload(version=reviewed_version, is_published='on',
                                                     confirm_publication='on', change_reason='Проверенная версия'))
        self.assertContains(response, 'Запись уже изменена')
        item.refresh_from_db()
        self.assertFalse(item.is_published)
