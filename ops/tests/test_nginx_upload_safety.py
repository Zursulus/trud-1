from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'ops' / 'apply-nginx-upload-safety.sh'


class NginxUploadSafetyTests(unittest.TestCase):
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

    def test_transform_adds_limit_and_friendly_413_once(self):
        source = '''server {
    server_name trud-1.ru www.trud-1.ru;
    location ^~ /admin/ {
        proxy_pass http://127.0.0.1:8002;
    }
}
server {
    server_name trud-1.ru www.trud-1.ru;
    return 404;
}
'''
        rendered = self.transform(source)
        self.assertEqual(rendered.count('# ZUR-112 portal upload safety'), 1)
        self.assertIn('client_max_body_size 12m;', rendered)
        self.assertIn('error_page 413 = @trud_payload_too_large;', rendered)
        self.assertIn('Максимальный размер вложения — 10 МБ.', rendered)
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
