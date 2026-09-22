from datetime import date
import tempfile

from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings

from .models import Account, AppealCategory, ResidentAccess, ResidentAppeal, User
from .resident_models import (
    ResidentAppealAttachment,
    ResidentAppealBoardMessage,
    ResidentAppealViewState,
)


class ResidentConversationTests(TestCase):
    password = 'resident-conversation-password-2026!'

    def setUp(self):
        self.account = Account.objects.create(number='CHAT-1', plot='Садовая, 42')
        self.other_account = Account.objects.create(number='CHAT-2', plot='Чужой участок')
        self.category = AppealCategory.objects.create(name='Документы')
        self.resident = User.objects.create_user(
            username='chat-resident@example.test', email='chat-resident@example.test', password=self.password,
        )
        self.other_resident = User.objects.create_user(
            username='chat-other@example.test', email='chat-other@example.test', password=self.password,
        )
        ResidentAccess.objects.create(user=self.resident, account=self.account, role='owner', starts=date(2026, 1, 1))
        ResidentAccess.objects.create(user=self.other_resident, account=self.other_account, role='owner', starts=date(2026, 1, 1))
        self.appeal = ResidentAppeal.objects.create(
            account=self.account,
            author=self.resident,
            category=self.category,
            subject='Нужен документ',
            message='Прошу проверить документ.',
        )

    def test_resident_upload_is_private_and_cross_account_download_is_404(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.client.force_login(self.resident)
            response = self.client.post(
                f'/admin/cabinet/account/{self.account.pk}/appeal/{self.appeal.pk}/',
                {
                    'body': 'Прикладываю подтверждение.',
                    'attachment': SimpleUploadedFile('подтверждение.pdf', b'%PDF-private', content_type='application/pdf'),
                },
            )
            self.assertEqual(response.status_code, 302)
            attachment = ResidentAppealAttachment.objects.get()
            self.assertEqual(attachment.uploaded_by, self.resident)
            self.assertTrue(attachment.document.name.startswith('appeal-attachments/'))

            url = f'/admin/cabinet/account/{self.account.pk}/appeal/{self.appeal.pk}/attachment/{attachment.pk}/'
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn('private, no-store', response['Cache-Control'])
            self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(b''.join(response.streaming_content), b'%PDF-private')

            self.client.force_login(self.other_resident)
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_unsafe_attachment_type_is_rejected_without_writing_file_record(self):
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.client.force_login(self.resident)
            response = self.client.post(
                f'/admin/cabinet/account/{self.account.pk}/appeal/{self.appeal.pk}/',
                {
                    'body': 'Плохой тип файла.',
                    'attachment': SimpleUploadedFile('страница.html', b'<script>x</script>', content_type='text/html'),
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'Разрешены только PDF, JPG и PNG')
            self.assertEqual(ResidentAppealAttachment.objects.count(), 0)

    def test_board_messages_are_immutable_unread_until_opened_and_can_repeat(self):
        manager = User.objects.create_user(username='board-manager', password=self.password, is_staff=True)
        first = ResidentAppealBoardMessage.objects.create(
            appeal=self.appeal, author=manager, body='Первый ответ правления.',
        )
        self.client.force_login(self.resident)
        list_url = f'/admin/cabinet/account/{self.account.pk}/appeals/'
        response = self.client.get(list_url)
        self.assertContains(response, 'Новый ответ')
        self.assertFalse(ResidentAppealViewState.objects.filter(appeal=self.appeal).exists())

        dialog_url = f'/admin/cabinet/account/{self.account.pk}/appeal/{self.appeal.pk}/'
        response = self.client.get(dialog_url)
        self.assertContains(response, 'Первый ответ правления.')
        state = ResidentAppealViewState.objects.get(user=self.resident, appeal=self.appeal)
        self.assertEqual(state.last_seen_response_at, first.created_at)
        self.assertNotContains(self.client.get(list_url), 'Новый ответ')

        second = ResidentAppealBoardMessage.objects.create(
            appeal=self.appeal, author=manager, body='Второй ответ правления.',
        )
        self.assertGreaterEqual(second.created_at, first.created_at)
        self.assertContains(self.client.get(list_url), 'Новый ответ')

        second.body = 'Попытка переписать отправленный ответ.'
        with self.assertRaises(Exception):
            second.save()

    def test_board_workspace_sends_message_and_private_attachment(self):
        call_command('setup_roles')
        manager = User.objects.create_user(username='board-workspace-manager', password=self.password, is_staff=True)
        manager.groups.add(Group.objects.get(name='Администратор ТСН'))
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            self.client.force_login(manager)
            response = self.client.post(
                f'/admin/water/residentappeal/{self.appeal.pk}/attachments/',
                {
                    'body': 'Документ подготовлен.',
                    'document': SimpleUploadedFile('ответ.png', b'PNG-private', content_type='image/png'),
                },
            )
            self.assertEqual(response.status_code, 302)
            message = ResidentAppealBoardMessage.objects.get(appeal=self.appeal)
            attachment = ResidentAppealAttachment.objects.get(board_message=message)
            self.assertEqual(attachment.uploaded_by, manager)

            download = self.client.get(f'/admin/water/residentappeal/attachment/{attachment.pk}/')
            self.assertEqual(download.status_code, 200)
            self.assertIn('private, no-store', download['Cache-Control'])

            self.client.force_login(self.resident)
            response = self.client.get(f'/admin/cabinet/account/{self.account.pk}/appeal/{self.appeal.pk}/')
            self.assertContains(response, 'Документ подготовлен.')
            self.assertContains(response, 'ответ.png')
