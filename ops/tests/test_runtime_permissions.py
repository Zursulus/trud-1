from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
DROPIN = ROOT / 'ops' / 'trud-1-site-private-data.conf'
SETTINGS = ROOT / 'backend' / 'config' / 'settings.py'
STATIC_STORAGE = ROOT / 'backend' / 'config' / 'storage.py'


class RuntimePermissionContractTests(unittest.TestCase):
    def test_private_data_is_the_only_opt_path_made_writable(self):
        text = DROPIN.read_text(encoding='utf-8')
        writable = [
            line.split('=', 1)[1].strip()
            for line in text.splitlines()
            if line.strip().startswith('ReadWritePaths=')
        ]
        self.assertEqual(writable, ['/opt/trud-1-site/private-data'])

    def test_django_media_root_matches_systemd_writable_path(self):
        settings = SETTINGS.read_text(encoding='utf-8')
        self.assertIn("MEDIA_ROOT = BASE_DIR.parent / 'private-data'", settings)

    def test_private_uploads_keep_restrictive_permissions(self):
        settings = SETTINGS.read_text(encoding='utf-8')
        self.assertIn('FILE_UPLOAD_PERMISSIONS = 0o600', settings)
        self.assertIn('FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700', settings)

    def test_collectstatic_uses_public_readable_permissions(self):
        settings = SETTINGS.read_text(encoding='utf-8')
        storage = STATIC_STORAGE.read_text(encoding='utf-8')
        self.assertIn("'staticfiles': {'BACKEND': 'config.storage.PublicStaticFilesStorage'}", settings)
        self.assertIn("file_permissions_mode', 0o644", storage)
        self.assertIn("directory_permissions_mode', 0o755", storage)


if __name__ == '__main__':
    unittest.main()
