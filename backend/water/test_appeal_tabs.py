from datetime import date

from django.test import TestCase

from .models import Account, AppealCategory, ResidentAccess, ResidentAppeal, User


class ResidentAppealTabsTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='TABS-1', plot='Садовая, 77')
        self.user = User.objects.create_user(username='tabs@example.test', password='tabs-password-2026!')
        ResidentAccess.objects.create(user=self.user, account=self.account, role='owner', starts=date(2026, 1, 1))
        category = AppealCategory.objects.create(name='Документы')
        self.open_new = self._appeal(category, 'Новое', 'new')
        self.open_work = self._appeal(category, 'В работе', 'in_progress')
        self.open_wait = self._appeal(category, 'Ждёт жителя', 'awaiting_resident')
        self.resolved = self._appeal(category, 'Решено', 'resolved', response='Готово')
        self.closed = self._appeal(category, 'Закрыто', 'closed', response='Закрыто')
        self.client.force_login(self.user)

    def _appeal(self, category, subject, status, response=''):
        return ResidentAppeal.objects.create(
            account=self.account,
            author=self.user,
            category=category,
            subject=subject,
            message='Текст обращения',
            status=status,
            response=response,
        )

    def _get(self, state=None):
        url = f'/admin/cabinet/account/{self.account.pk}/appeals/'
        return self.client.get(url, {'state': state} if state else {})

    def test_tabs_filter_and_counts(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['appeal_state'], 'all')
        self.assertEqual(response.context['appeal_open_count'], 3)
        self.assertEqual(response.context['appeal_resolved_count'], 2)
        self.assertContains(response, 'name="state"')
        self.assertContains(response, 'value="open"')
        self.assertContains(response, 'value="resolved"')

        response = self._get('open')
        self.assertContains(response, 'Новое')
        self.assertContains(response, 'В работе')
        self.assertContains(response, 'Ждёт жителя')
        self.assertNotContains(response, '>Решено<')
        self.assertNotContains(response, '>Закрыто<')

        response = self._get('resolved')
        self.assertContains(response, 'Решено')
        self.assertContains(response, 'Закрыто')
        self.assertNotContains(response, '>Новое<')
        self.assertNotContains(response, '>В работе<')

    def test_unknown_state_falls_back_to_all(self):
        response = self._get('broken')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['appeal_state'], 'all')
        self.assertEqual(len(response.context['appeals']), 5)
