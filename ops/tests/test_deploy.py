"""Execute the installer as an ordinary user, with all server operations faked."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'deploy-compatible.sh'
OLD = 'a' * 40
NEW = 'b' * 40
MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, signal, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['FAKE_ROOT'])
with (root / 'calls').open('a') as f:
    f.write(name + ' ' + ' '.join(args) + '\n')
mode = os.environ.get('FAIL', '')
if name == 'id': print(0)
elif name == 'runuser':
    cmd = args[args.index('--') + 1:]
    if cmd[0] == 'pg_dump':
        if mode == 'dump': sys.exit(1)
        print('fake dump')
    else:
        cmd = cmd[3:]
        if cmd[0] == 'status': print('?? local' if mode == 'dirty' else '')
        elif cmd[0] == 'rev-parse': print('c' * 40 if mode == 'head' else 'a' * 40)
        elif cmd[0] == 'merge-base' and mode == 'diverged': sys.exit(1)
        elif cmd[0] == 'diff': print('backend/requirements.txt' if mode == 'incompatible' else '')
        elif cmd[0] == 'checkout': (root / 'head').write_text(cmd[-1])
elif name == 'systemd-run':
    current = (root / 'head').read_text()
    if mode == 'signal' and args[-1] == 'check' and current == 'b' * 40:
        os.kill(os.getppid(), signal.SIGTERM)
    if mode == 'check' and args[-1] == 'check' and current == 'b' * 40: sys.exit(1)
    if mode == 'migration' and args[-2:] == ['migrate', '--check']: sys.exit(1)
    if mode == 'rollback' and 'collectstatic' in args: sys.exit(1)
elif name == 'curl':
    current = (root / 'head').read_text()
    if args[-1].endswith('deployment-status/'):
        data = json.loads((root / 'state/deployment-status.json').read_text())
        if mode == 'marker': data['commit'] = 'c' * 40
        print(json.dumps(data))
    elif mode == 'smoke' and current == 'b' * 40: print('503')
    else: print('302' if args[-1].endswith('/admin/') else '200')
elif name == 'systemctl':
    if mode == 'start' and args[0] == 'start' and (root / 'head').read_text() == 'b' * 40: sys.exit(1)
    if args[0] in ('start', 'stop'): (root / 'service').write_text(args[0])
elif name == 'pg_restore' and mode == 'dump_validation': sys.exit(1)
'''


class DeployTests(unittest.TestCase):
    def execute(self, failure=''):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name in ('bin', 'app', 'state', 'backups', 'run'):
            (root / name).mkdir()
        (root / 'head').write_text(OLD)
        self.marker = json.dumps({'project': 'trud-1', 'commit': OLD, 'deployed_at': '2026-09-18T10:00:00Z'})
        (root / 'state/deployment-status.json').write_text(self.marker)
        for name in ('id', 'runuser', 'systemd-run', 'systemctl', 'curl', 'pg_restore', 'chown', 'sleep'):
            file = root / 'bin' / name
            file.write_text(MOCK)
            file.chmod(0o755)
        script = SOURCE.read_text()
        for source, target in (('/opt/trud-1-site', root / 'app'),
                               ('/var/lib/trud-1', root / 'state'),
                               ('/var/backups', root / 'backups'),
                               ('/run/trud-backup.lock', root / 'run/lock')):
            script = script.replace(source, str(target))
        file = root / 'deploy.sh'
        file.write_text(script)
        result = subprocess.run(['bash', str(file), NEW, OLD], env={**os.environ,
            'PATH': str(root / 'bin') + ':' + os.environ['PATH'],
            'FAKE_ROOT': str(root), 'FAIL': failure}, capture_output=True, text=True)
        return root, result, (root / 'calls').read_text()

    def test_success(self):
        root, result, calls = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((root / 'head').read_text(), NEW)
        self.assertEqual(json.loads((root / 'state/deployment-status.json').read_text())['commit'], NEW)
        self.assertLess(calls.index('systemctl stop'), calls.index('pg_dump'))
        self.assertIn('migrate --check', calls)
        self.assertNotIn('migrate --noinput', calls)
        self.assertEqual((root / 'service').read_text(), 'start')

    def test_preflight_refuses_without_stopping_service(self):
        for failure in ('dirty', 'head', 'diverged', 'incompatible'):
            with self.subTest(failure=failure):
                root, result, calls = self.execute(failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('systemctl stop', calls)
                self.assertNotIn('pg_dump', calls)
                self.assertEqual((root / 'head').read_text(), OLD)

    def test_backup_failure_restarts_unchanged_release(self):
        for failure in ('dump', 'dump_validation'):
            with self.subTest(failure=failure):
                root, result, calls = self.execute(failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('checkout', calls)
                self.assertEqual((root / 'service').read_text(), 'start')

    def test_failed_release_restores_code_static_and_exact_marker(self):
        for failure in ('check', 'migration', 'smoke', 'marker', 'signal', 'start'):
            with self.subTest(failure=failure):
                root, result, calls = self.execute(failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((root / 'head').read_text(), OLD)
                self.assertEqual((root / 'state/deployment-status.json').read_text(), self.marker)
                self.assertEqual((root / 'service').read_text(), 'start')
                self.assertIn('collectstatic --noinput', calls)
                self.assertEqual(result.stdout.count('Установка не завершена'), 1)

    def test_rollback_failure_is_not_reported_as_success(self):
        root, result, calls = self.execute('rollback')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Откат не подтверждён', result.stderr)
        self.assertNotIn('Сохранена версия', result.stdout)
        self.assertEqual((root / 'service').read_text(), 'stop')


if __name__ == '__main__':
    unittest.main()
