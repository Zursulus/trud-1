"""Execute the administrator launcher with isolated host-operation stubs."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

OPS = Path(__file__).resolve().parents[1]


class ReviewedReleaseTests(unittest.TestCase):
    def execute(self, failure=""):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bins = root / "bin"
            bins.mkdir()
            stage = root / "stages"
            stage.mkdir()
            marker = root / "marker.json"
            marker.write_text('{"project":"trud-1","commit":"27f62efcb8381e34f3de895c9fc41d1613c9cfd0"}')
            log = root / "calls"
            stub = '''#!/usr/bin/python3
import os, pathlib, shutil, sys
name = pathlib.Path(sys.argv[0]).name
a = sys.argv[1:]
failure = os.environ.get('FAILURE', '')
with open(os.environ['CALLS'], 'a') as f: f.write(name + ' ' + ' '.join(a) + '\\n')
if name == 'id': print('0')
elif name == 'runuser':
    a = a[a.index('--') + 1:]
    if a[0] == 'git':
        if 'rev-parse' in a: print('wrong' if failure == 'drift' else '27f62efcb8381e34f3de895c9fc41d1613c9cfd0')
    elif a[0] == 'psql': print('1000')
    elif a[0].endswith('clamdscan'):
        infected = 'EICAR' in sys.stdin.read()
        sys.exit(2 if failure == 'scanner' else int(infected))
elif name == 'du': print('1000\\t.')
elif name == 'df': print('Avail\\n' + ('1' if failure == 'space' else '9999999999'))
elif name == 'curl':
    if '--output' in a:
        dest = pathlib.Path(a[a.index('--output') + 1])
        shutil.copyfile(pathlib.Path(os.environ['SOURCE_OPS']) / dest.name, dest)
        if failure == 'checksum': dest.write_text('corrupt')
    else: print('{}')
elif name == 'bash':
    if a[0] == '-n': os.execv('/bin/bash', ['/bin/bash', *a])
    if failure in ('backup', 'bootstrap', 'smoke'):
        needle = {'backup':'backup-trud-site', 'bootstrap':'install-deploy', 'smoke':'smoke-deploy'}[failure]
        if needle in a[0]: sys.exit(7)
elif name == 'deploy': sys.exit(3 if failure == 'postcheck' else 0)
'''
            for name in ("id", "runuser", "du", "df", "curl", "bash", "systemctl", "nginx", "deploy"):
                path = bins / name
                path.write_text(stub)
                path.chmod(0o700)
            source = (OPS / "release-reviewed-157.sh").read_text()
            source = source.replace('export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', f'export PATH={bins}:/usr/bin:/bin')
            source = source.replace('/var/lib/trud-1/deployment-status.json', str(marker))
            source = source.replace('/root/trud-release-157.', str(stage / 'release.'))
            source = source.replace('/var/backups/trud-1-release-157.', str(stage / 'backup.'))
            source = source.replace('/usr/local/sbin/deploy-trud-compatible\n', str(bins / 'deploy') + '\n')
            launcher = root / "launcher"
            launcher.write_text(source)
            result = subprocess.run(['/bin/bash', str(launcher)], text=True, capture_output=True,
                env={**os.environ, 'FAILURE': failure, 'CALLS': str(log), 'SOURCE_OPS': str(OPS)})
            return result, log.read_text()

    def test_success_requires_backup_bootstrap_smoke_before_release(self):
        result, calls = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        positions = [calls.index(x) for x in ('bash ./backup-trud-site', 'bash ./install-deploy', 'bash ./smoke-deploy', 'deploy ')]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('RELEASE_157=PASS', result.stdout)

    def test_failure_gates_never_start_deploy(self):
        for failure in ('drift', 'space', 'scanner', 'checksum', 'backup', 'bootstrap', 'smoke'):
            with self.subTest(failure=failure):
                result, calls = self.execute(failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('\ndeploy ', '\n' + calls)
                self.assertNotIn('RELEASE_157=PASS', result.stdout)

    def test_late_failure_is_not_reported_as_success(self):
        result, calls = self.execute('postcheck')
        self.assertEqual(result.returncode, 3)
        self.assertNotIn('RELEASE_157=PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
