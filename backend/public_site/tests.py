from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import PublicationAdminForm
from .models import PublicDocument, PublicDocumentCategory, PublicNews


class NewsPublicationForm(PublicationAdminForm):
    class Meta(PublicationAdminForm.Meta):
        model = PublicNews
        fields = '__all__'


class PublicContentTests(TestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.media.cleanup)

    def test_public_feed_exposes_only_explicitly_checked_published_news(self):
        today = timezone.localdate()
        visible = PublicNews.objects.create(
            title='Открытая новость', category='ОБЪЯВЛЕНИЕ', summary='Коротко', body='Полный текст',
            published_on=today, is_published=True, public_checked=True,
        )
        PublicNews.objects.create(
            title='Черновик', category='НОВОСТЬ', summary='Не показывать', body='Черновик',
            published_on=today, is_published=False, public_checked=False,
        )
        PublicNews.objects.create(
            title='Не подтверждено', category='НОВОСТЬ', summary='Не показывать', body='Текст',
            published_on=today, is_published=True, public_checked=False,
        )
        PublicNews.objects.create(
            title='Будущая новость', category='НОВОСТЬ', summary='Рано', body='Текст',
            published_on=today + timedelta(days=1), is_published=True, public_checked=True,
        )

        response = self.client.get(reverse('public_content'))
        self.assertEqual(response.status_code, 200)
        news = response.json()['news']
        self.assertEqual([item['id'] for item in news], [visible.pk])
        self.assertNotIn('published_by', news[0])

    def test_public_document_feed_and_download_require_publish_gate(self):
        category = PublicDocumentCategory.objects.create(name='Протоколы')
        visible = PublicDocument.objects.create(
            category=category,
            title='Протокол собрания',
            description='Открытый документ',
            document=SimpleUploadedFile('protocol.txt', b'public text', content_type='text/plain'),
            document_date=timezone.localdate(),
            is_published=True,
            public_checked=True,
        )
        hidden = PublicDocument.objects.create(
            category=category,
            title='Служебный черновик',
            document=SimpleUploadedFile('draft.txt', b'secret draft', content_type='text/plain'),
            document_date=timezone.localdate(),
            is_published=True,
            public_checked=False,
        )

        response = self.client.get(reverse('public_content'))
        documents = response.json()['documents']
        self.assertEqual([item['id'] for item in documents], [visible.pk])
        self.assertEqual(documents[0]['url'], reverse('public_document_download', args=[visible.pk]))

        download = self.client.get(reverse('public_document_download', args=[visible.pk]))
        self.assertEqual(download.status_code, 200)
        self.assertIn('attachment;', download['Content-Disposition'])
        self.assertEqual(download['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(b''.join(download.streaming_content), b'public text')
        self.assertEqual(
            self.client.get(reverse('public_document_download', args=[hidden.pk])).status_code,
            404,
        )

    def test_public_document_rejects_html_like_upload(self):
        category = PublicDocumentCategory.objects.create(name='Документы')
        item = PublicDocument(
            category=category,
            title='Опасный файл',
            document=SimpleUploadedFile('page.html', b'<script>alert(1)</script>', content_type='text/html'),
            document_date=timezone.localdate(),
        )
        with self.assertRaises(ValidationError):
            item.full_clean()

    def test_publication_form_requires_explicit_confirmation(self):
        data = {
            'title': 'Новость',
            'category': 'НОВОСТЬ',
            'summary': 'Анонс',
            'body': 'Текст',
            'published_on': timezone.localdate().isoformat(),
            'is_published': 'on',
        }
        form = NewsPublicationForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('confirm_publication', form.errors)

        data['confirm_publication'] = 'on'
        form = NewsPublicationForm(data=data)
        self.assertTrue(form.is_valid(), form.errors)

    def test_static_frontend_is_wired_to_public_feed(self):
        root = Path(__file__).resolve().parents[2]
        index = (root / 'index.html').read_text(encoding='utf-8')
        script = (root / 'app.js').read_text(encoding='utf-8')
        public_css = (root / 'public-content.css').read_text(encoding='utf-8')
        self.assertIn('id="documents-list"', index)
        self.assertIn('public-content.css', index)
        self.assertIn('ВАЖНО ДЛЯ ЧЛЕНОВ ТСН', index)
        self.assertIn('Оформление прав на земельные участки', index)
        self.assertIn('feodosia-letter-2026-08-27.webp', index)
        self.assertNotIn('Вечер в Орджоникидзе', index)
        self.assertNotIn('ordzhonikidze-sunset.webp', index)
        self.assertIn('Участок ваш.', index)
        self.assertIn('id="article-meta"', index)
        self.assertIn('id="article-summary"', index)
        self.assertIn("/admin/public/content/", script)
        self.assertIn('renderArticleBody', script)
        self.assertNotIn('trud1-featured-news:', script)
        self.assertIn('overflow-y:auto', public_css)
