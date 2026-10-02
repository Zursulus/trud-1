[Reading 202 lines from start (total: 202 lines, 0 remaining)]

from datetime import timedelta
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .appeal_security import enforce_resident_submission_limits, validate_appeal_attachment
from .models import Account, AppealCategory, ResidentAccess, ResidentAppeal, ResidentAppealMessage, User
from .resident_models import ResidentAppealAttachment
from .security_models import SecurityAlert


@override_settings(APPEAL_MALWARE_SCAN_REQUIRED=False, APPEAL_CLAMDSCAN_PATH='')
class AppealAttachmentSecurityTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='SEC-1', plot='Тест безопасности')
        self.resident = User.objects.create_user(username='security-resident', password='safe-password-2026!')
        ResidentAccess.objects.create(
            user=self.resident,
            account=self.account,
            role='owner',
            starts=timezone.localdate() - timedelta(days=1),
        )
        self.category = AppealCategory.objects.create(name='Безопасность')
        self.client.force_login(self.resident)

    def _office_bytes(self, kind, *, dangerous=False):
        stream = BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('[Content_Types].xml', '<Types/>')
            archive.writestr('_rels/.rels', '<Relationships/>')
            if kind == 'docx':
                archive.writestr('word/document.xml', '<document/>')
                if dangerous:
                    archive.writestr('word/vbaProject.bin', b'macro')
            else:
                archive.writestr('xl/workbook.xml', '<workbook/>')
                if dangerous:
                    archive.writestr('xl/embeddings/oleObject1.bin', b'object')
        return stream.getvalue()

    def test_safe_docx_and_xlsx_are_accepted(self):
        for name, kind in (('statement.docx', 'docx'), ('table.xlsx', 'xlsx')):
            upload = SimpleUploadedFile(name, self._office_bytes(kind))
            self.assertIs(validate_appeal_attachment(upload), upload)
            upload.seek(0)

    def test_spoofed_docx_is_rejected(self):
        upload = SimpleUploadedFile('payload.docx', b'MZ-not-a-zip')
        with self.assertRaises(ValidationError) as caught:
            validate_appeal_attachment(upload)
        self.assertEqual(caught.exception.code, 'appeal_content_mismatch')

    def test_macro_or_embedded_office_content_is_rejected(self):
        for name, kind in (('macro.docx', 'docx'), ('embedded.xlsx', 'xlsx')):
            upload = SimpleUploadedFile(name, self._office_bytes(kind, dangerous=True))
            with self.assertRaises(ValidationError) as caught:
                validate_appeal_attachment(upload)
            self.assertEqual(caught.exception.code, 'appeal_dangerous_office')

    def test_resident_can_create_appeal_with_docx(self):
        upload = SimpleUploadedFile('statement.docx', self._office_bytes('docx'))
        response = self.client.post(
            reverse('resident_appeal_new', args=[self.account.pk]),
            {
                'category': self.category.pk,
                'subject': 'Документ',
                'message': 'Прикладываю документ.',
                'attachment': upload,
            },
        )
        self.assertEqual(response.status_code, 302)
        attachment = ResidentAppealAttachment.objects.get()
        self.assertEqual(attachment.original_name, 'statement.docx')
        self.assertTrue(attachment.document.name.endswith('.docx'))

    def test_spoofed_upload_creates_admin_alert_and_is_not_saved(self):
        response = self.client.post(
            reverse('resident_appeal_new', args=[self.account.pk]),
            {
                'category': self.category.pk,
                'subject': 'Плохой файл',
                'message': 'Проверка.',
                'attachment': SimpleUploadedFile('payload.docx', b'MZ-not-a-zip'),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ResidentAppeal.objects.count(), 0)
        alert = SecurityAlert.objects.get()
        self.assertEqual(alert.kind, SecurityAlert.KIND_SUSPICIOUS_FILE)
        self.assertEqual(alert.actor, self.resident)
        self.assertEqual(alert.account, self.account)
        self.assertEqual(alert.original_name, 'payload.docx')

    def test_plain_unsupported_legacy_doc_does_not_create_security_noise(self):
        response = self.client.post(
            reverse('resident_appeal_new', args=[self.account.pk]),
            {
                'category': self.category.pk,
                'subject': 'Старый формат',
                'message': 'Проверка.',
                'attachment': SimpleUploadedFile('legacy.doc', b'not-supported'),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SecurityAlert.objects.count(), 0)

    @override_settings(APPEAL_MALWARE_SCAN_REQUIRED=True, APPEAL_CLAMDSCAN_PATH='/fake/clamdscan')
    @patch('water.appeal_security._scanner_path', return_value='/fake/clamdscan')
    @patch('water.appeal_security.subprocess.run')
    def test_detected_malware_creates_critical_alert(self, run, _scanner_path):
        run.return_value = SimpleNamespace(returncode=1, stdout='FOUND', stderr='')
        response = self.client.post(
            reverse('resident_appeal_new', args=[self.account.pk]),
            {
                'category': self.category.pk,
                'subject': 'Вирус',
                'message': 'Проверка.',
                'attachment': SimpleUploadedFile('clean-looking.pdf', b'%PDF-1.7\nbody'),
            },
        )
        self.assertEqual(response.status_code, 200)
        alert = SecurityAlert.objects.get()
        self.assertEqual(alert.kind, SecurityAlert.KIND_MALWARE)
        self.assertEqual(alert.severity, SecurityAlert.SEVERITY_CRITICAL)
        self.assertEqual(ResidentAppeal.objects.count(), 0)
        command = run.call_args.args[0]
        self.assertNotIn('--fdpass', command)
        self.assertIn('--stream', command)
        self.assertIn('--no-summary', command)


@override_settings(APPEAL_MALWARE_SCAN_REQUIRED=False, APPEAL_CLAMDSCAN_PATH='')
class AppealSpamAlertTests(TestCase):
    def setUp(self):
        self.account = Account.objects.create(number='SPAM-1', plot='Антиспам')
        self.resident = User.objects.create_user(username='spam-resident')
        ResidentAccess.objects.create(
            user=self.resident,
            account=self.account,
            role='owner',
            starts=timezone.localdate() - timedelta(days=1),
        )
        category = AppealCategory.objects.create(name='Спам тест')
        self.appeal = ResidentAppeal.objects.create(
            account=self.account,
            author=self.resident,
            category=category,
            subject='Тест',
            message='Первое сообщение',
        )
        for index in range(4):
            ResidentAppealMessage.objects.create(
                appeal=self.appeal,
                author=self.resident,
                body=f'Сообщение {index}',
            )
        self.request = RequestFactory().post('/resident/appeal/')
        self.request.META['REMOTE_ADDR'] = '203.0.113.10'

    def test_message_rate_limit_alerts_once_per_window(self):
        for _ in range(2):
            with self.assertRaises(ValidationError) as caught:
                enforce_resident_submission_limits(
                    request=self.request,
                    user=self.resident,
                    account=self.account,
                    appeal=self.appeal,
                )
            self.assertEqual(caught.exception.code, 'appeal_rate_limited')
        self.assertEqual(SecurityAlert.objects.filter(kind=SecurityAlert.KIND_SPAM).count(), 1)

    def test_admin_role_sees_security_queue_without_resident_username(self):
        call_command('setup_roles', stdout=StringIO())
        admin_user = User.objects.create_user(username='security-admin', is_staff=True)
        admin_user.groups.add(Group.objects.get(name='Администратор ТСН'))
        enforce_error = None
        try:
            enforce_resident_submission_limits(
                request=self.request,
                user=self.resident,
                account=self.account,
                appeal=self.appeal,
            )
        except ValidationError as error:
            enforce_error = error
        self.assertIsNotNone(enforce_error)

        self.client.force_login(admin_user)
        response = self.client.get(reverse('staff_workspace:security'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Подозрение на спам')
        self.assertContains(response, f'Пользователь #{self.resident.pk}')
        self.assertNotContains(response, 'spam-resident')
        self.assertContains(response, 'SPAM-1')

[executed on device: sandbox (2ce8fd8f-c8b1-4737-95b3-20fa4189189e)]