"""Offline CLI checks: no real services, checkout, network or production data."""
from pathlib import Path
import json
import os
import re
import subprocess
import tempfile
import unittest

OPS = Path(__file__).resolve().parents[1]
RELEASE = OPS / 'release-redesign-20261017.sh'
ROLLBACK = OPS / 'rollback-redesign-20261017.sh'


class RedesignScriptsTests(unittest.TestCase):
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
        for args in ([], ['/tmp/unsafe'], ['/var/backups/trud-release-20261017.x', 'extra'],
                     ['/var/backups/trud-release-20261017.x/../other']):
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


if __name__ == '__main__':
    unittest.main()
