"""Public navigation regressions against local static files and synthetic feeds.

Run: python backend/manage.py test public_site.e2e_tests -v 2
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from django.test import SimpleTestCase
from playwright.sync_api import expect, sync_playwright


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_DIR = REPOSITORY_ROOT / 'backend' / 'test-artifacts'
FEED = {
    'news': [{
        'id': 7, 'title': 'Проверка публичной новости', 'category': 'Объявление',
        'date': '2026-10-01', 'summary': 'Синтетический анонс для проверки.',
        'body': 'Первый абзац.\n\n> Цитата\n\n- Первый пункт\n- Второй пункт',
        'featured': True,
    }],
    'documents': [{
        'id': 2, 'title': 'Тестовый открытый документ', 'category': 'Документы',
        'description': 'Синтетический документ.', 'date': '2026-10-01',
        'url': '/admin/public/document/2/',
    }],
}


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


class PublicContentBrowserTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.server = ThreadingHTTPServer(
            ('127.0.0.1', 0), partial(QuietStaticHandler, directory=str(REPOSITORY_ROOT)),
        )
        cls.url = f'http://127.0.0.1:{cls.server.server_port}/index.html'
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        super().tearDownClass()

    def _feed(self, page, payload=FEED):
        page.route('**/admin/public/content/', lambda route: route.fulfill(json=payload))

    def _assert_no_overflow(self, page):
        self.assertTrue(page.evaluate(
            'document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1'
        ))

    def test_article_history_close_escape_back_forward_and_focus(self):
        with sync_playwright() as playwright:
            for engine in (playwright.chromium, playwright.webkit):
                browser = engine.launch(headless=True)
                try:
                    for width in (390, 1280):
                        with self.subTest(browser=engine.name, width=width):
                            context = browser.new_context(viewport={'width': width, 'height': 844})
                            try:
                                page = context.new_page()
                                errors = []
                                page.on('pageerror', lambda error: errors.append(str(error)))
                                self._feed(page)
                                page.goto(self.url)
                                page.locator('nav a[href="#documents"]').click()
                                expect(page).to_have_url(self.url + '#documents')
                                initial_history = page.evaluate('history.length')
                                trigger = page.locator('[data-news-id="7"]')
                                trigger.click()
                                expect(page).to_have_url(self.url + '?news=7#documents')
                                expect(page.locator('#news-dialog')).to_be_visible()
                                expect(page.locator('#article-title')).to_have_text(FEED['news'][0]['title'])
                                self.assertEqual(page.evaluate('history.length'), initial_history + 1)
                                page.go_back()
                                expect(page).to_have_url(self.url + '#documents')
                                expect(page.locator('#news-dialog')).not_to_be_visible()
                                expect(trigger).to_be_focused()
                                page.go_forward()
                                expect(page.locator('#news-dialog')).to_be_visible()
                                page.keyboard.press('Escape')
                                expect(page).to_have_url(self.url + '#documents')
                                expect(page.locator('#news-dialog')).not_to_be_visible()
                                expect(trigger).to_be_focused()
                                trigger.click()
                                self.assertEqual(page.evaluate('history.length'), initial_history + 1)
                                page.get_by_role('button', name='Закрыть', exact=True).click()
                                expect(page).to_have_url(self.url + '#documents')
                                expect(trigger).to_be_focused()
                                page.go_forward()
                                expect(page.locator('#news-dialog')).to_be_visible()
                                page.mouse.click(1, 1)
                                expect(page).to_have_url(self.url + '#documents')
                                expect(page.locator('#news-dialog')).not_to_be_visible()
                                page.go_back()
                                expect(page).to_have_url(self.url)
                                expect(page.locator('#news-dialog')).not_to_be_visible()
                                navigation = page.locator('nav a[href="#important"]')
                                navigation.click()
                                expect(page).to_have_url(self.url + '#important')
                                # Native anchor navigation may focus its section rather than the link.
                                expect(trigger).not_to_be_focused()
                                self.assertEqual(errors, [])
                            finally:
                                context.close()
                finally:
                    browser.close()

    def test_direct_link_reload_unknown_article_and_close_stay_on_site(self):
        with sync_playwright() as playwright:
            for engine in (playwright.chromium, playwright.webkit):
                browser = engine.launch(headless=True)
                try:
                    page = browser.new_page()
                    self._feed(page)
                    page.goto(self.url + '?news=7#news')
                    expect(page.locator('#article-title')).to_have_text(FEED['news'][0]['title'])
                    page.reload()
                    expect(page.locator('#article-title')).to_have_text(FEED['news'][0]['title'])
                    page.keyboard.press('Escape')
                    expect(page).to_have_url(self.url + '#news')
                    expect(page.locator('#news-dialog')).not_to_be_visible()
                    page.goto(self.url + '?news=999#news')
                    expect(page.locator('#article-title')).to_have_text('Новость недоступна')
                    page.get_by_role('button', name='Закрыть', exact=True).click()
                    expect(page).to_have_url(self.url + '#news')
                    expect(page.locator('#news-dialog')).not_to_be_visible()
                finally:
                    browser.close()

    def test_loading_empty_error_retry_and_late_response_do_not_show_fake_news(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                pending = []
                page.route('**/admin/public/content/', lambda route: pending.append(route))
                page.goto(self.url + '?news=7', wait_until='domcontentloaded')
                expect(page.locator('#news-grid')).to_have_attribute('aria-busy', 'true')
                expect(page.locator('#news-grid')).to_have_text('Загружаем новости…')
                expect(page.locator('#documents-list')).to_have_text('Загружаем документы…')
                self.assertEqual(page.locator('.news-card').count(), 0)
                expect(page.locator('#article-title')).to_have_text('Загружаем новость…')
                page.keyboard.press('Escape')
                expect(page).to_have_url(self.url)
                page.wait_for_function('document.querySelector("#news-dialog").open === false')
                self.assertEqual(len(pending), 1)
                pending.pop().fulfill(json=FEED)
                expect(page.locator('.news-card')).to_have_count(1)
                expect(page.locator('#news-dialog')).not_to_be_visible()
                self.assertNotIn('Следующий материал', page.locator('body').inner_text())
                page.unroute('**/admin/public/content/')
                self._feed(page, {'news': [], 'documents': []})
                page.goto(self.url)
                expect(page.locator('#news-grid')).to_have_text('Пока нет опубликованных новостей.')
                expect(page.locator('#documents-list')).to_have_text('Пока нет опубликованных документов.')
                self.assertEqual(page.locator('.news-card').count(), 0)
                page.unroute('**/admin/public/content/')
                attempts = []

                def response(route):
                    attempts.append(True)
                    if len(attempts) == 1:
                        route.fulfill(status=503, body='Unavailable')
                    else:
                        route.fulfill(json=FEED)

                page.route('**/admin/public/content/', response)
                page.goto(self.url)
                expect(page.locator('#news-grid')).to_contain_text('Не удалось загрузить новости.')
                expect(page.locator('#documents-list')).to_contain_text('Не удалось загрузить документы.')
                self.assertEqual(page.locator('.news-card').count(), 0)
                page.locator('#news-grid').get_by_role('button', name='Повторить загрузку').click()
                expect(page.locator('.news-card')).to_have_count(1)
                expect(page.locator('.document-row')).to_have_count(1)
                expect(page.locator('#news-grid')).to_have_attribute('aria-busy', 'false')
                self.assertEqual(len(attempts), 2)
            finally:
                browser.close()

    def test_mobile_navigation_targets_single_card_layout_and_screenshots(self):
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for width in (320, 390, 1280):
                    context = browser.new_context(viewport={'width': width, 'height': 844})
                    try:
                        page = context.new_page()
                        self._feed(page)
                        page.goto(self.url)
                        expect(page.locator('.news-card')).to_have_count(1)
                        for href in ('#important', '#news', '#documents'):
                            link = page.locator(f'nav a[href="{href}"]')
                            expect(link).to_be_visible()
                            self.assertGreaterEqual(link.bounding_box()['height'], 44)
                        self._assert_no_overflow(page)
                        grid = page.locator('#news-grid').bounding_box()
                        card = page.locator('.news-card').bounding_box()
                        self.assertAlmostEqual(grid['width'], card['width'], delta=1)
                        page.screenshot(path=str(ARTIFACT_DIR / f'public-home-{width}.png'), full_page=True)
                        page.locator('[data-news-id="7"]').click()
                        expect(page.locator('#news-dialog')).to_be_visible()
                        self.assertGreaterEqual(page.locator('.close').bounding_box()['height'], 44)
                        self._assert_no_overflow(page)
                        page.screenshot(path=str(ARTIFACT_DIR / f'public-article-{width}.png'))
                    finally:
                        context.close()
            finally:
                browser.close()
