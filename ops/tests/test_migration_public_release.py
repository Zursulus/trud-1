"""Run the real migration installer; fake host services, use real atomic rename."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'deploy-migration-aware.sh'
LIVE = '6fb32eff0f389eda944371d802cd14dec8eb2adb'
INTENT = 'account:0001_initial,account:0002_email_max_length,account:0003_alter_emailaddress_create_unique_verified_email,account:0004_alter_emailaddress_drop_unique_email,account:0005_emailaddress_idx_upper_email,account:0006_emailaddress_lower,account:0007_emailaddress_idx_email,account:0008_emailaddress_unique_primary_email_fixup,account:0009_emailaddress_unique_primary_email,forum:0001_initial,public_site:0002_publicimage_alter_publicnews_is_featured_and_more'
NEW_SENSITIVE = ('backend/config/settings.py', 'backend/forum/migrations/0001_initial.py',
                 'backend/forum/migrations/__init__.py',
                 'backend/public_site/migrations/0002_publicimage_alter_publicnews_is_featured_and_more.py',
                 'backend/water/management/commands/setup_roles.py')
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
elif name == 'trud-1-backup':
    # Model the standard backup's real lock: inherited installer lock deadlocks.
    import fcntl
    with (r / 'run/lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if mode == 'verified_backup': sys.exit(1)
        print('Резервная копия проверена контрольным восстановлением: synthetic-backup')
elif name == 'runuser':
    cmd = args[args.index('--') + 1:]
    if cmd[0].endswith('/python'):
        sys.exit(1 if mode == 'dependency' else 0)
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
        elif cmd[0] == 'diff': print(os.environ['SENSITIVE'] + ('\nbackend/water/migrations/9999_unexpected.py' if mode == 'unexpected_delta' else ''))
        elif cmd[0] == 'show':
            file = cmd[-1].split(':', 1)[1]
            if file == 'backend/requirements.txt':
                print('Django==5.2.17')
                if cmd[-1].startswith('b' * 40): print('django-allauth==65.18.0')
                sys.exit(0)
            if mode == 'missing_asset' and file == 'style.css': sys.exit(1)
            print('new ' + file)
        elif cmd[0] == 'checkout':
            (r / 'head').write_text(cmd[-1])
            for file in os.environ['FILES'].split(','):
                (r / 'app' / file).write_text(('new ' if cmd[-1] == 'b' * 40 else 'old ') + file + '\n')
elif name == 'systemd-run':
    if any(a.endswith('/trud-1-backup') for a in args):
        import subprocess
        sys.exit(subprocess.call([args[-1]]))
    current = (r / 'head').read_text()
    if 'showmigrations' in args:
        print('[X] 0001_initial\n[X] 0033_residentaccessrequest_requester_user\n[X] 0034_security_alert')
    if 'shell' in args:
        if 'MigrationExecutor' in args[-1]:
            print('unexpected:0001' if mode == 'plan' else os.environ.get('INTENT', ''))
        elif mode == 'flags': sys.exit(1)
        else: print('RELEASE_FLAGS=PASS')
    if current == 'b' * 40 and mode == 'migration' and args[-2:] == ['migrate', '--noinput']: sys.exit(1)
    if current == os.environ.get('OLD_COMMIT', 'a' * 40) and mode == 'rollback' and 'collectstatic' in args: sys.exit(1)
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
    elif url.endswith(('/accounts/signup/', '/accounts/login/', '/accounts/login/code/', '/accounts/login/code/confirm/')):
        print('200' if mode == 'email_open' else '404')
    elif url.endswith('/admin/cabinet/forum/'):
        print('200' if mode == 'public_forum' else '302')
    elif mode == 'smoke' and current == 'b' * 40: print('503')
    else: print('302' if url.endswith('/admin/') else '200')
elif name == 'pg_restore' and mode == 'dump_validation': sys.exit(1)
'''

class MigrationPublicReleaseTests(unittest.TestCase):
    def execute(self, mode='', new_release=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        r = Path(temporary.name)
        for name in ('bin', 'app', 'state', 'backups', 'run', 'public', 'nginx'):
            (r / name).mkdir()
        public = r / 'public/bef1cb7'
        public.mkdir()
        self.old_commit = LIVE if new_release else OLD
        self.marker = json.dumps({'project': 'trud-1', 'commit': self.old_commit, 'deployed_at': '2026-10-02T14:26:51Z'})
        (r / 'state/deployment-status.json').write_text(self.marker)
        (r / 'head').write_text(self.old_commit)
        (r / 'service').write_text('start')
        for file in FILES:
            (public / file).write_text('old ' + file + '\n')
            (r / 'app' / file).write_text('old ' + file + '\n')
        self.old_inode = public.stat().st_ino
        (r / 'nginx/site').write_text('server {\n root ' + str(public) + ';\n}\n')
        if mode == 'bad_root': (r / 'nginx/site').write_text('root /unexpected;\n')
        if mode == 'duplicate_root':
            (r / 'nginx/site').write_text('root ' + str(public) + ';\nroot ' + str(public) + ';\n')
        for name in ('id', 'runuser', 'systemd-run', 'systemctl', 'curl', 'pg_restore', 'chown', 'sleep', 'nginx', 'trud-1-backup', 'journalctl'):
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
                               ('/usr/bin/clamdscan', scanner),
                               ('/usr/local/sbin/trud-1-backup', r / 'bin/trud-1-backup')):
            script = script.replace(source, str(target))
        (r / 'deploy.sh').write_text(script)
        result = subprocess.run(['bash', str(r / 'deploy.sh'), NEW, self.old_commit] + (['release-20261016'] if new_release else []),
            env={**os.environ, 'PATH': str(r / 'bin') + ':' + os.environ['PATH'],
                 'FAKE_ROOT': str(r), 'FAIL': mode, 'FILES': ','.join(FILES),
                 'OLD_COMMIT': self.old_commit, 'INTENT': INTENT,
                 'SENSITIVE': '\n'.join(NEW_SENSITIVE if new_release else SENSITIVE)}, capture_output=True, text=True, timeout=25)
        return r, result, (r / 'calls').read_text()

    def assert_old(self, r):
        self.assertEqual((r / 'head').read_text(), self.old_commit)
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

class OctoberReleaseTests(unittest.TestCase):
    execute = MigrationPublicReleaseTests.execute
    assert_old = MigrationPublicReleaseTests.assert_old

    def test_success_keeps_public_files_and_inode_and_runs_verified_backup(self):
        r, result, calls = self.execute(new_release=True)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual((r / 'head').read_text(), NEW)
        self.assertEqual((r / 'public/bef1cb7').stat().st_ino, self.old_inode)
        for file in FILES:
            self.assertEqual((r / 'public/bef1cb7' / file).read_text(), 'old ' + file + '\n')
        self.assertEqual(list((r / 'public').glob('.trud-*')), [])
        self.assertIn('public_static=unchanged', result.stdout)
        self.assertLess(calls.index('trud-1-backup'), calls.index('systemctl stop'))
        self.assertLess(calls.index('pg_dump'), calls.index('checkout'))
        self.assertLess(calls.index('checkout'), calls.index('migrate --noinput'))
        self.assertIn('RELEASE_FLAGS=PASS', result.stdout)

    def test_verified_backup_or_preflight_failure_never_changes_live_code(self):
        for mode in ('verified_backup', 'dependency', 'dirty', 'unexpected_delta'):
            with self.subTest(mode=mode):
                r, result, calls = self.execute(mode, new_release=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('checkout', calls)
                self.assertNotIn('systemctl stop', calls)
                self.assertNotIn('migrate --noinput', calls)
                self.assert_old(r)

    def test_plan_and_flags_fail_before_migrate_and_restore_code(self):
        for mode in ('plan', 'flags'):
            with self.subTest(mode=mode):
                r, result, calls = self.execute(mode, new_release=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('migrate --noinput', calls)
                self.assert_old(r)
                self.assertEqual((r / 'service').read_text(), 'start')

    def test_failure_restores_code_marker_and_keeps_public_untouched(self):
        for mode in ('dump', 'dump_validation', 'migration', 'scanner', 'start', 'smoke',
                     'signal', 'email_open', 'public_forum'):
            with self.subTest(mode=mode):
                r, result, calls = self.execute(mode, new_release=True)
                self.assertNotEqual(result.returncode, 0)
                self.assert_old(r)
                self.assertEqual((r / 'service').read_text(), 'start')
                self.assertNotIn('migrate water 0033', calls)
                self.assertIn('journalctl', calls)

    def test_late_postcheck_failure_keeps_target_and_reports_status_3(self):
        r, result, calls = self.execute('late', new_release=True)
        self.assertEqual(result.returncode, 3)
        self.assertIn('COMMITTED_POSTCHECK_FAILED', result.stderr)
        self.assertEqual((r / 'head').read_text(), NEW)
        self.assertEqual((r / 'public/bef1cb7').stat().st_ino, self.old_inode)

if __name__ == '__main__': unittest.main()
