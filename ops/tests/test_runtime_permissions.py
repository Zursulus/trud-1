from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
DROPIN = ROOT / 'ops' / 'trud-1-site-private-data.conf'
SETTINGS = ROOT / 'backend' / 'config' / 'settings.py'


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


if __name__ == '__main__':
    unittest.main()
