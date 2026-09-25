from datetime import date

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .imports import MAX_FILE_SIZE, stage_import
from .models import ImportBatch


class AdminImportUploadLimitTests(TestCase):
    def test_oversized_import_is_rejected_before_staging(self):
        upload = SimpleUploadedFile(
            'too-large.csv',
            b'x' * (MAX_FILE_SIZE + 1),
            content_type='text/csv',
        )

        with self.assertRaisesMessage(ValidationError, 'Файл больше 5 МБ.'):
            stage_import(upload, date(2026, 1, 1))

        self.assertFalse(ImportBatch.objects.exists())
