"""Run both installed broker entrypoints with a root-only synthetic manifest."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_migration_public_release import INTENT, LIVE, NEW

OPS = Path(__file__).resolve().parents[1]
MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
r = pathlib.Path(os.environ['FAKE_ROOT'])
a = sys.argv[1:]
name = pathlib.Path(sys.argv[0]).name
mode = os.environ.get('FAIL', '')
with (r / 'calls').open('a') as f: f.write(name + ' ' + ' '.join(a) + '\n')
if name == 'id': print(1000 if mode == 'not_root' else 0)
elif name == 'stat':
    if a[1] == '%u': print(1000 if mode == 'owner' else 0)
    else: print('666' if mode == 'writable' else '600')
elif name == 'runuser':
    cmd = a[a.index('--')+1:][3:]
    if cmd[0] == 'status': print('?? dirty' if mode == 'dirty' else '')
    elif cmd[0] == 'rev-parse':
        if cmd[1] == 'HEAD': print((r / 'head').read_text())
        else: print('c'*40 if mode == 'branch' else os.environ['TARGET'])
    elif cmd[0] == 'merge-base' and mode == 'ancestor': sys.exit(1)
    elif cmd[0] == 'show': sys.stdout.write((r / 'helper').read_text())
elif name == 'curl':
    if a[-1].endswith('deployment-status/'):
        if mode == 'postcheck': sys.exit(1)
        print((r / 'state/deployment-status.json').read_text())
    else: print(200)
elif name == 'systemctl' and mode == 'service': sys.exit(1)
'''


class OctoberReleaseBrokerTests(unittest.TestCase):
    def execute(self, mode='', entrypoint='deploy-compatible.sh'):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        r = Path(temporary.name)
        for name in ('bin', 'app', 'backups', 'state', 'config'):
            (r / name).mkdir()
        (r / 'head').write_text(LIVE)
        (r / 'state/deployment-status.json').write_text(json.dumps({'project': 'trud-1', 'commit': LIVE}))
        helper = '''#!/bin/bash
set -eu
printf '%s' "$1" > "$FAKE_ROOT/head"
printf '{"project":"trud-1","commit":"%s"}' "$1" > "$FAKE_ROOT/state/deployment-status.json"
test "$3" = release-20261016
test "${FAIL:-}" != helper_late || exit 3
'''
        (r / 'helper').write_text(helper)
        digest = hashlib.sha256(helper.encode()).hexdigest()
        if mode == 'checksum': digest = '0' * 64
        manifest = (
            f'EXPECTED_LIVE_SHA={LIVE}\nTARGET_SHA={NEW}\nBRANCH=claude/prod-release\n'
            f'DEPLOY_SCRIPT_SHA256={digest}\nRELEASE_KIND=release-20261016\n'
            f"MIGRATION_INTENT='{INTENT}'\nSETUP_ROLES_INTENT=required\n"
            'SCANNER_INTENT=required-before-start\n'
            f'printf touched > {r}/sourced\n'
        )
        if mode == 'intent': manifest = manifest.replace(INTENT, 'water:0034_security_alert')
        (r / 'config/release.conf').write_text(manifest)
        for name in ('id', 'stat', 'runuser', 'curl', 'systemctl'):
            executable = r / 'bin' / name
            executable.write_text(MOCK)
            executable.chmod(0o755)
        script = (OPS / entrypoint).read_text()
        for source, target in (('/etc/system-maintenance/trud-release.conf', r / 'config/release.conf'),
                               ('/etc/system-maintenance', r / 'config'),
                               ('/opt/trud-1-site', r / 'app'), ('/var/lib/trud-1', r / 'state'),
                               ('/var/backups', r / 'backups')):
            script = script.replace(source, str(target))
        (r / 'broker.sh').write_text(script)
        result = subprocess.run(['bash', str(r / 'broker.sh')], capture_output=True, text=True,
            env={**os.environ, 'PATH': str(r / 'bin') + ':' + os.environ['PATH'],
                 'FAKE_ROOT': str(r), 'FAIL': mode, 'TARGET': NEW}, timeout=10)
        return r, result, (r / 'calls').read_text()

    def test_both_entrypoints_deploy_the_pinned_helper(self):
        for name in ('deploy-compatible.sh', 'deploy-trud-compatible.sh'):
            with self.subTest(entrypoint=name):
                r, result, calls = self.execute(entrypoint=name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('DEPLOY_TRUD_COMPATIBLE=PASS', result.stdout)
                self.assertEqual((r / 'head').read_text(), NEW)

    def test_untrusted_manifest_is_not_sourced(self):
        for mode in ('not_root', 'owner', 'writable'):
            with self.subTest(mode=mode):
                r, result, calls = self.execute(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((r / 'sourced').exists())
                self.assertNotIn('runuser', calls)

    def test_guard_failures_leave_head_unchanged(self):
        for mode in ('intent', 'dirty', 'ancestor', 'branch', 'checksum'):
            with self.subTest(mode=mode):
                r, result, calls = self.execute(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((r / 'head').read_text(), LIVE)

    def test_late_failures_report_committed_target(self):
        for mode in ('postcheck', 'service', 'helper_late'):
            with self.subTest(mode=mode):
                r, result, calls = self.execute(mode)
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn('COMMITTED_POSTCHECK_FAILED', result.stderr)
                self.assertEqual((r / 'head').read_text(), NEW)
