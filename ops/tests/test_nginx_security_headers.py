from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'ops' / 'apply-nginx-security-headers.sh'


class NginxSecurityHeadersTests(unittest.TestCase):
    def transform(self, source):
        result = subprocess.run(
            ['bash', str(SCRIPT), '--transform'],
            input=source,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_transform_adds_baseline_headers_once(self):
        source = '''server {
    server_name trud-1.ru www.trud-1.ru;
    location / {
        try_files $uri /index.html;
    }
}
'''
        rendered = self.transform(source)
        self.assertEqual(rendered.count('# ZUR-120 baseline security headers'), 1)
        self.assertIn('Strict-Transport-Security "max-age=31536000" always;', rendered)
        self.assertIn('X-Content-Type-Options "nosniff" always;', rendered)
        self.assertIn('Referrer-Policy "strict-origin-when-cross-origin" always;', rendered)
        self.assertIn('X-Frame-Options "SAMEORIGIN" always;', rendered)
        self.assertEqual(self.transform(rendered), rendered)

    def test_transform_fails_when_expected_server_is_missing(self):
        result = subprocess.run(
            ['bash', str(SCRIPT), '--transform'],
            input='server { server_name example.test; }\n',
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Не найден server_name trud-1.ru', result.stderr)


if __name__ == '__main__':
    unittest.main()
