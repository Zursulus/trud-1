"""Offline CLI checks: no real services, checkout, network or production data."""
from pathlib import Path
import json
import os
import re
import subprocess
import tempfile
import unittest

OPS = Path(__file__).resolve().parents[1]
RELEASE = OPS / 'release-redesign-20261019.sh'
ROLLBACK = OPS / 'rollback-redesign-20261019.sh'


class Redesign20261019ScriptsTests(unittest.TestCase):
    def test_bash_syntax(self):
        for script in (RELEASE, ROLLBACK):
            result = subprocess.run(['bash', '-n', str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_arguments_fail_before_server_commands(self):
        cases = [[], ['--help'], ['--apply'], ['--other', 'a' * 40],
                 ['--check', '57dbe9c'], ['--apply', 'A' * 40],
                 ['--check', 'a' * 40, 'extra']]
        for args in cases:
            with self.subTest(args=args):
                result = subprocess.run(['bash', str(RELEASE), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('Использование:', result.stderr)
        for args in ([], ['/tmp/unsafe'], ['/var/backups/trud-release-20261019.x', 'extra'],
                     ['/var/backups/trud-release-20261019.x/../other']):
            result = subprocess.run(['bash', str(ROLLBACK), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('Использование:', result.stderr)

    def run_failure(self, started, action, rollback_exit=0):
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory)
            (backup / 'rollback.sh').write_text(
                '#!/bin/bash\nset -Eeuo pipefail\nprintf called > "$1/called"\n'
                f'exit {rollback_exit}\n')
            # Only the definitions; never run server preflight in this harness.
            definitions = RELEASE.read_text().split('trap cleanup EXIT')[0]
            harness = definitions + '\nBACKUP=$1\nCHANGING=$2\ntrap failed ERR INT TERM\n' + action
            result = subprocess.run(['bash', '-c', harness, 'test', directory, str(started)],
                                    capture_output=True, text=True)
            return result, (backup / 'called').exists()

    def test_failure_before_changes_does_not_rollback(self):
        result, called = self.run_failure(0, 'false\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(called)
        self.assertIn('ОШИБКА', result.stderr)

    def test_failure_after_changes_rolls_back_and_stays_nonzero(self):
        result, called = self.run_failure(1, 'false\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(called)

    def test_signals_after_changes_roll_back(self):
        for sig in ('INT', 'TERM'):
            with self.subTest(signal=sig):
                result, called = self.run_failure(1, f'kill -{sig} $$\n')
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(called)

    def test_failed_rollback_is_reported(self):
        result, called = self.run_failure(1, 'false\n', rollback_exit=7)
        self.assertTrue(called)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('откат не подтверждён', result.stderr)

    def test_atomic_exchange_and_repeatable_restore(self):
        script = RELEASE.read_text()
        function = script[script.index('exchange() {'):script.index("step 'Конфигурация")]
        restore = re.findall(r"<<'PY'\n(.*?)\nPY", ROLLBACK.read_text(), re.S)[1]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, stage, backup, marker = [base / p for p in ('root', 'stage', 'backup', 'marker')]
            for path in (root, stage, backup, marker):
                path.mkdir()
            (root / 'index').write_text('old')
            (stage / 'index').write_text('new')
            old_id = f'{root.stat().st_dev}:{root.stat().st_ino}'
            new_id = f'{stage.stat().st_dev}:{stage.stat().st_ino}'
            (backup / 'deployment-status.json').write_text('{"commit":"old"}')
            result = subprocess.run(['bash', '-c', 'set -Eeuo pipefail\n' + function + '\nexchange "$1" "$2"',
                                     'test', str(root), str(stage)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'index').read_text(), 'new')
            restore = restore.replace('/var/lib/trud-1', str(marker))
            for _ in range(2):
                result = subprocess.run(['python3', '-c', restore, str(root), str(stage),
                                         old_id, new_id, str(backup)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((root / 'index').read_text(), 'old')
                self.assertEqual((stage / 'index').read_text(), 'new')
                self.assertEqual(json.loads((marker / 'deployment-status.json').read_text())['commit'], 'old')
            # A third directory is never blindly exchanged into production.
            result = subprocess.run(['python3', '-c', restore, str(root), str(stage),
                                     '0:0', new_id, str(backup)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / 'index').read_text(), 'old')

    def test_empty_plan_rejects_pending_operations(self):
        script = RELEASE.read_text()
        function = script[script.index('empty_plan() {'):script.index('exchange() {')]
        for plan, success in [('Planned operations:\n  No planned migration operations.', True),
                              ('Planned operations:\nwater.0034_security_alert', False),
                              ('No planned migration operations.\nwater.0034', False)]:
            harness = ('set -Eeuo pipefail\nmanage_at() { if [[ $3 == --plan ]]; then printf "%s\\n" "$PLAN"; fi; }\n'
                       + function + '\nempty_plan /unused\n')
            result = subprocess.run(['bash', '-c', harness], env={**os.environ, 'PLAN': plan},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode == 0, success, result.stderr)

    def test_exact_baseline_dates_and_ssh_command_are_preserved(self):
        sha = 'ef8e510a825e4a4af641d6fb700d48417f8d05f7'
        release, rollback = RELEASE.read_text(), ROLLBACK.read_text()
        self.assertIn('EXPECTED_SHA=' + sha, release)
        self.assertIn("h=='" + sha + "'", release)
        self.assertIn('$EXPECTED_SHA == ' + sha, rollback)
        self.assertIn('ops/rollback-redesign-20261019.sh', release)
        self.assertIn('SSHCMD=$(gitapp config --get core.sshCommand)', release)
        self.assertIn('gitstage -c core.sshCommand="$SSHCMD" fetch', release)
        for script in (release, rollback):
            self.assertNotIn('57dbe9c', script)
            self.assertIn('trud-release-20261019.', script)
            self.assertIn('.trud-redesign-20261019.', script)
        self.assertIn('trud-release-check-20261019.', release)

    def test_baseline_requires_exact_sha_in_all_three_sources(self):
        function = re.findall(r"<<'PY'\n(.*?)\nPY", RELEASE.read_text(), re.S)[0]
        sha = 'ef8e510a825e4a4af641d6fb700d48417f8d05f7'
        other = sha[:-1] + '0'
        import unittest.mock as mock
        from io import StringIO
        for marker, http, head in [(sha, sha, sha), (other, other, other),
                                    (sha, other, sha), (other, sha, sha), (sha, sha, other)]:
            with self.subTest(marker=marker, http=http, head=head):
                def output(args, **kwargs):
                    if args[0] == 'curl':
                        return json.dumps({'project': 'trud-1', 'commit': http}).encode()
                    return head + '\n'
                with mock.patch('sys.argv', ['test', '/unused']), \
                     mock.patch('builtins.open', mock.mock_open(read_data=json.dumps({'project': 'trud-1', 'commit': marker}))), \
                     mock.patch('subprocess.check_output', side_effect=output), \
                     mock.patch('sys.stdout', new_callable=StringIO):
                    if marker == http == head == sha:
                        exec(function, {})
                    else:
                        with self.assertRaises(AssertionError):
                            exec(function, {})

    def test_diff_guard_allows_ui_and_rejects_incompatible_files(self):
        script = RELEASE.read_text()
        guard = script[script.index('[[ -z $(gitstage diff --name-only'):script.index('gitstage diff --stat')]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            work = base / 'work'
            work.mkdir()
            # Isolated scratch repository; the project's .git is never accessed.
            git = ['git', '--git-dir=' + str(base / 'repository'), '--work-tree=' + str(work)]
            def run(*args):
                return subprocess.run([*git, *args], check=True, capture_output=True, text=True)
            run('init', '--quiet')
            # Compare Git trees directly: no commits or checkout, even in scratch.
            expected = run('write-tree').stdout.strip()
            paths = {
                'backend/templates/includes/internal_header.html': True,
                'backend/water/templates/water/work/dashboard.html': True,
                'backend/water/static/water/internal-nav.css': True,
                'backend/water/static/water/internal-nav.js': True,
                'backend/water/navigation.py': True,
                'backend/water/staff_workspace.py': True,
                'backend/water/test_navigation.py': True,
                'ops/release-redesign-20261019.sh': True,
                'ops/rollback-redesign-20261019.sh': True,
                'ops/tests/test_redesign_20261019_scripts.py': True,
                'docs/RELEASE-20261019.md': True,
                'answer.md': True,
                'backend/requirements.txt': False,
                'backend/water/migrations/9999_test.py': False,
                'backend/water/management/commands/test.py': False,
                'ops/backup-trud-site.sh': False,
                'package.json': False,
            }
            for path, allowed in paths.items():
                with self.subTest(path=path):
                    file = work / path
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_text('synthetic change')
                    run('add', path)
                    target = run('write-tree').stdout.strip()
                    harness = 'set -Eeuo pipefail\ngitstage() { git "$@"; }\n' + guard
                    result = subprocess.run(['bash', '-c', harness], capture_output=True, text=True,
                        env={**os.environ, 'GIT_DIR': str(base / 'repository'), 'GIT_WORK_TREE': str(work),
                             'EXPECTED_SHA': expected, 'TARGET_SHA': target})
                    self.assertEqual(result.returncode == 0, allowed, result.stderr)
                    run('read-tree', expected)
                    file.unlink()


if __name__ == '__main__':
    unittest.main()
