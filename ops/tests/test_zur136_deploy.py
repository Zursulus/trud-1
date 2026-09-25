from pathlib import Path
import subprocess
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'deploy-zur136-business-audit.sh'
EXPECTED = 'ffb1808aa55f0bd4b749b0b83deed22a3cbc3d97'


class Zur136DeployGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding='utf-8')

    def test_shell_syntax(self):
        result = subprocess.run(['bash', '-n', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_release_is_pinned_and_target_must_be_on_integration_branch(self):
        self.assertIn(f'EXPECTED={EXPECTED}', self.text)
        self.assertIn('gitapp fetch origin feature/water-admin', self.text)
        self.assertIn('gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"', self.text)
        self.assertIn('gitapp merge-base --is-ancestor "$TARGET" origin/feature/water-admin', self.text)

    def test_only_expected_files_are_allowed(self):
        for path in (
            'backend/water/test_performance_baseline.py',
            'backend/water/management/commands/audit_business_integrity.py',
            'backend/water/test_business_integrity_audit.py',
            'ops/deploy-zur136-business-audit.sh',
            'ops/tests/test_zur136_deploy.py',
        ):
            self.assertIn(path, self.text)

    def test_verified_backup_precedes_checkout(self):
        self.assertIn('pg_dump -Fc trud_site', self.text)
        self.assertIn('pg_restore --list', self.text)
        self.assertIn('private-data.tar.gz', self.text)
        self.assertLess(self.text.index('pg_dump -Fc trud_site'), self.text.index('gitapp checkout --detach "$TARGET"'))

    def test_rollback_and_public_marker_are_verified(self):
        self.assertIn('gitapp checkout --detach "$EXPECTED"', self.text)
        self.assertIn('deployment-status.json', self.text)
        self.assertIn('https://trud-1.ru/admin/deployment-status/', self.text)
        self.assertIn('test "$status_commit" = "$TARGET"', self.text)


if __name__ == '__main__':
    unittest.main()
