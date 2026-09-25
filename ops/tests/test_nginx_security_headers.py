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

    def source(self):
        return '''server {
    server_name trud-1.ru www.trud-1.ru;
    location @trud_payload_too_large {
        add_header Cache-Control "no-store" always;
        return 413 'too large';
    }
    location ^~ /admin/ {
        proxy_pass http://127.0.0.1:8002;
    }
    location ^~ /admin-static/ {
        alias /opt/trud-1-site/backend/staticfiles/;
    }
    location / {
        try_files $uri $uri/ =404;
    }
}
'''

    def test_transform_scopes_headers_to_nginx_owned_locations(self):
        rendered = self.transform(self.source())
        self.assertEqual(rendered.count('# ZUR-120 nginx-owned response headers'), 3)
        admin_proxy = rendered.split('location ^~ /admin/ {', 1)[1].split('}', 1)[0]
        self.assertNotIn('ZUR-120 nginx-owned response headers', admin_proxy)
        self.assertEqual(self.transform(rendered), rendered)

    def test_transform_removes_old_server_wide_block(self):
        old = '''
    # ZUR-120 baseline security headers. Keep this deliberately conservative:
    # these headers harden both the nginx-served public root and proxied Django
    # routes without imposing a CSP that could break the existing interface.
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
'''
        source = self.source().replace(
            '    server_name trud-1.ru www.trud-1.ru;\n',
            '    server_name trud-1.ru www.trud-1.ru;\n' + old,
        )
        rendered = self.transform(source)
        self.assertNotIn('# ZUR-120 baseline security headers', rendered)
        self.assertEqual(rendered.count('# ZUR-120 nginx-owned response headers'), 3)

    def test_transform_fails_when_required_location_is_missing(self):
        result = subprocess.run(
            ['bash', str(SCRIPT), '--transform'],
            input='server {\n    server_name trud-1.ru www.trud-1.ru;\n}\n',
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Не найден ожидаемый nginx location', result.stderr)


if __name__ == '__main__':
    unittest.main()
