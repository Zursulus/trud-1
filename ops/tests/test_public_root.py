"""Regression tests for ZUR-42 Nginx public-root discovery."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'deploy-public-content.sh'


class PublicRootResolutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        nginx = self.bin / 'nginx'
        nginx.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
        nginx.chmod(0o755)

        script = SOURCE.read_text(encoding='utf-8')
        match = re.search(r'(resolve_public_root\(\) \{.*?^\})\nPUBLIC_ROOT=', script, re.S | re.M)
        self.assertIsNotNone(match, 'resolve_public_root function not found')
        self.function = match.group(1)

    def execute(self, configured_root: Path):
        site = self.root / 'trud-1.nginx'
        site.write_text(
            'server {\n    server_name trud-1.ru;\n    root %s;\n}\n' % configured_root,
            encoding='utf-8',
        )
        allowed_prefix = self.root / 'var/www/trud-1/releases'
        function = self.function.replace(
            '/var/www/trud-1/releases/',
            str(allowed_prefix) + '/',
        )
        shell = f'''set -euo pipefail
NGINX_SITE={site!s}
{function}
resolve_public_root
'''
        return subprocess.run(
            ['bash', '-c', shell],
            env={**os.environ, 'PATH': str(self.bin) + ':' + os.environ['PATH']},
            capture_output=True,
            text=True,
        )

    def test_accepts_existing_release_root_from_nginx_config(self):
        release = self.root / 'var/www/trud-1/releases/bef1cb7'
        release.mkdir(parents=True)
        result = self.execute(release)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(release.resolve()))

    def test_rejects_root_outside_release_tree(self):
        unexpected = self.root / 'var/www/html'
        unexpected.mkdir(parents=True)
        result = self.execute(unexpected)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Неожиданный public root Nginx', result.stderr)


if __name__ == '__main__':
    unittest.main()
