from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'ops' / 'apply-journald-retention.sh'


class JournaldRetentionTests(unittest.TestCase):
    def test_render_is_exact_and_bounded(self):
        result = subprocess.run(
            ['bash', str(SCRIPT), '--render'],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            '[Journal]\n'
            'SystemMaxUse=512M\n'
            'SystemKeepFree=1G\n'
            'MaxRetentionSec=30day\n',
        )


if __name__ == '__main__':
    unittest.main()
