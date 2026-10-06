"""Run the real migration installer; fake host services, use real atomic rename."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'deploy-migration-aware.sh'
OLD, NEW = 'a' * 40, 'b' * 40
FILES = ('index.html', 'app.js', 'style.css', 'public-content.css',
         'feodosia-letter-2026-08-27.html', 'feodosia-letter-2026-08-27.webp',
         'landscape.webp', 'ordzhonikidze-sunset.webp')
SENSITIVE = ('backend/config/settings.py', 'backend/water/management/commands/setup_roles.py',
             'backend/water/management/commands/vtb_registry_dryrun.py',
             'backend/water/migrations/0034_security_alert.py')
MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, signal, sys
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
r = pathlib.Path(os.environ['FAKE_ROOT'])
mode = os.environ.get('FAIL', '')
with (r / 'calls').open('a') as f: f.write(name + ' ' + ' '.join(args) + '\n')
if name == 'id': print(0)
elif name == 'runuser':
    cmd = args[args.index('--') + 1:]
    if cmd[0] == 'pg_dump':
        if mode == 'dump': sys.exit(1)
        print('fake dump')
    elif cmd[0].endswith('clamdscan'):
        if mode == 'scanner': sys.exit(1)
    else:
        cmd = cmd[3:]
        if cmd[0] == 'status': print('?? dirty' if mode == 'dirty' else '')
        elif cmd[0] == 'rev-parse':
            print((r / 'head').read_text() if cmd[-1] == 'HEAD' else 'blob')
        elif cmd[0] == 'diff': print(os.environ['SENSITIVE'])
        elif cmd[0] == 'show':
            file = cmd[-1].split(':', 1)[1]
            if mode == 'missing_asset' and file == 'style.css': sys.exit(1)
            print('new ' + file)
        elif cmd[0] == 'checkout':
            (r / 'head').write_text(cmd[-1])
            for file in os.environ['FILES'].split(','):
                (r / 'app' / file).write_text(('new ' if cmd[-1] == 'b' * 40 else 'old ') + file + '\n')
elif name == 'systemd-run':
    current = (r / 'head').read_text()
    if 'showmigrations' in args:
        print('[X] 0033_residentaccessrequest_requester_user\n[X] 0034_security_alert')
    if current == 'b' * 40 and mode == 'migration' and args[-2:] == ['migrate', '--noinput']: sys.exit(1)
    if current == 'a' * 40 and mode == 'rollback' and 'collectstatic' in args: sys.exit(1)
elif name == 'systemctl':
    current = (r / 'head').read_text()
    if args[0] == 'start' and current == 'b' * 40 and mode in ('start', 'rollback'): sys.exit(1)
    if args[0] in ('stop', 'start'): (r / 'service').write_text(args[0])
    if args[0] == 'start' and current == 'b' * 40 and mode == 'signal':
        os.kill(os.getppid(), signal.SIGTERM)
elif name == 'curl':
    current = (r / 'head').read_text()
    url = args[-1]
    if url.endswith('deployment-status/'):
        if mode == 'late': sys.exit(1)
        print((r / 'state/deployment-status.json').read_text())
    elif '?release=' in url:
        file = url.split('?')[0].rsplit('/', 1)[-1]
        if mode == 'stale_public': print('stale ' + file)
        else: sys.stdout.write((r / 'public' / 'bef1cb7' / file).read_text())
    elif mode == 'smoke' and current == 'b' * 40: print('503')
    else: print('302' if url.endswith('/admin/') else '200')
elif name == 'pg_restore' and mode == 'dump_validation': sys.exit(1)
'''

class MigrationPublicReleaseTests(unittest.TestCase):
    def execute(self, mode=''):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        r = Path(temporary.name)
        for name in ('bin', 'app', 'state', 'backups', 'run', 'public', 'nginx'):
            (r / name).mkdir()
        public = r / 'public/bef1cb7'
        public.mkdir()
        self.marker = json.dumps({'project': 'trud-1', 'commit': OLD, 'deployed_at': '2026-10-02T14:26:51Z'})
        (r / 'state/deployment-status.json').write_text(self.marker)
        (r / 'head').write_text(OLD)
        (r / 'service').write_text('start')
        for file in FILES:
            (public / file).write_text('old ' + file + '\n')
            (r / 'app' / file).write_text('old ' + file + '\n')
        self.old_inode = public.stat().st_ino
        (r / 'nginx/site').write_text('server {\n root ' + str(public) + ';\n}\n')
        if mode == 'bad_root': (r / 'nginx/site').write_text('root /unexpected;\n')
        if mode == 'duplicate_root':
            (r / 'nginx/site').write_text('root ' + str(public) + ';\nroot ' + str(public) + ';\n')
        for name in ('id', 'runuser', 'systemd-run', 'systemctl', 'curl', 'pg_restore', 'chown', 'sleep', 'nginx'):
            path = r / 'bin' / name
            path.write_text(MOCK)
            path.chmod(0o755)
        scanner = r / 'bin/clamdscan'
        scanner.write_text('#!/bin/sh\nexit 0\n')
        scanner.chmod(0o755)
        script = SOURCE.read_text()
        for source, target in (('/opt/trud-1-site', r / 'app'), ('/var/lib/trud-1', r / 'state'),
                               ('/var/backups', r / 'backups'), ('/run/trud-backup.lock', r / 'run/lock'),
                               ('/etc/nginx/sites-enabled/trud-1', r / 'nginx/site'),
                               ('/var/www/trud-1/releases', r / 'public'),
                               ('/usr/bin/clamdscan', scanner)):
            script = script.replace(source, str(target))
        (r / 'deploy.sh').write_text(script)
        result = subprocess.run(['bash', str(r / 'deploy.sh'), NEW, OLD],
            env={**os.environ, 'PATH': str(r / 'bin') + ':' + os.environ['PATH'],
                 'FAKE_ROOT': str(r), 'FAIL': mode, 'FILES': ','.join(FILES),
                 'SENSITIVE': '\n'.join(SENSITIVE)}, capture_output=True, text=True, timeout=25)
        return r, result, (r / 'calls').read_text()

    def assert_old(self, r):
        self.assertEqual((r / 'head').read_text(), OLD)
        self.assertEqual((r / 'state/deployment-status.json').read_text(), self.marker)
        public = r / 'public/bef1cb7'
        self.assertEqual(public.stat().st_ino, self.old_inode)
        for file in FILES: self.assertEqual((public / file).read_text(), 'old ' + file + '\n')

    def test_success_publishes_whole_directory_and_preserves_previous(self):
        r, result, calls = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((r / 'head').read_text(), NEW)
        self.assertEqual(json.loads((r / 'state/deployment-status.json').read_text())['commit'], NEW)
        for file in FILES: self.assertEqual((r / 'public/bef1cb7' / file).read_text(), 'new ' + file + '\n')
        previous = list((r / 'public').glob('.trud-public.*'))
        self.assertEqual(len(previous), 1)
        self.assertEqual(previous[0].stat().st_ino, self.old_inode)
        self.assertTrue(list((r / 'backups').glob('*/public-root.tar.gz')))
        self.assertLess(calls.index('nginx -t'), calls.index('systemctl stop'))
        self.assertLess(calls.index('pg_dump'), calls.index('checkout'))
        self.assertLess(calls.index('migrate --noinput'), calls.index('clamdscan --stream'))
        self.assertLess(calls.index('clamdscan --stream'), calls.index('systemctl start'))

    def test_public_preflight_refuses_before_service_stop(self):
        for mode in ('dirty', 'missing_asset', 'bad_root', 'duplicate_root'):
            with self.subTest(mode=mode):
                r, result, calls = self.execute(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('systemctl stop', calls)
                self.assert_old(r)

    def test_backup_failure_never_switches_code_or_public(self):
        for mode in ('dump', 'dump_validation'):
            with self.subTest(mode=mode):
                r, result, calls = self.execute(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('checkout', calls)
                self.assert_old(r)
                self.assertEqual((r / 'service').read_text(), 'start')

    def test_failures_restore_backend_public_and_original_marker(self):
        for mode in ('migration', 'scanner', 'start', 'smoke', 'stale_public', 'signal'):
            with self.subTest(mode=mode):
                r, result, calls = self.execute(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assert_old(r)
                self.assertEqual((r / 'service').read_text(), 'start')
                self.assertNotIn('migrate water 0033', calls)

    def test_late_failure_truthfully_keeps_committed_backend_and_public(self):
        r, result, calls = self.execute('late')
        self.assertEqual(result.returncode, 3)
        self.assertIn('COMMITTED_POSTCHECK_FAILED', result.stderr)
        self.assertEqual((r / 'head').read_text(), NEW)
        self.assertEqual((r / 'public/bef1cb7/index.html').read_text(), 'new index.html\n')
        self.assertEqual(json.loads((r / 'state/deployment-status.json').read_text())['commit'], NEW)
        self.assertNotIn('Установка не завершена', result.stdout)

    def test_failed_rollback_keeps_service_stopped_and_reports_uncertainty(self):
        r, result, calls = self.execute('rollback')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Откат не подтверждён', result.stderr)
        self.assertEqual((r / 'service').read_text(), 'stop')
        self.assertEqual((r / 'public/bef1cb7/index.html').read_text(), 'old index.html\n')

if __name__ == '__main__': unittest.main()
