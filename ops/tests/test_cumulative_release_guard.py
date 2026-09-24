"""Safety-contract tests for the one-off ZUR-96 cumulative production deploy."""
from pathlib import Path
import subprocess
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy-cumulative-zur96.sh"
EXPECTED_PRODUCTION = "48698fb2fd3bb2b3e4ac50473523ab763afe485f"
EXPECTED_MIGRATIONS = (
    "0021_resident_identity_tsn_membership",
    "0022_resident_access_request",
    "0023_portal_grant",
    "0024_charge_obligation",
    "0025_charge_obligation_person_plot_context",
    "0026_controller_line_access",
)


class CumulativeReleaseGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_shell_syntax(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_release_is_pinned_to_confirmed_production_and_expected_migrations(self):
        self.assertIn(f"EXPECTED_RELEASE={EXPECTED_PRODUCTION}", self.text)
        for migration in EXPECTED_MIGRATIONS:
            self.assertIn(migration, self.text)
        self.assertIn('gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"', self.text)
        self.assertIn('gitapp merge-base --is-ancestor "$TARGET" origin/feature/water-admin', self.text)

    def test_high_risk_runtime_files_are_guarded(self):
        for path in (
            "backend/requirements.txt",
            "backend/config/settings.py",
            "ops/backup-trud-site.sh",
            "ops/trud-1-backup.service",
            "ops/trud-1-backup.timer",
            "ops/trud-1-site-private-data.conf",
        ):
            self.assertIn(path, self.text)

    def test_backup_precedes_code_switch_and_dump_is_validated(self):
        self.assertIn("pg_dump -Fc trud_site", self.text)
        self.assertIn("pg_restore --list", self.text)
        self.assertIn("private-data.tar.gz", self.text)
        self.assertLess(self.text.index("pg_dump -Fc trud_site"), self.text.index('gitapp checkout --detach "$TARGET"'))

    def test_rollback_does_not_reverse_additive_schema(self):
        self.assertNotIn("migrate water 0020", self.text)
        self.assertIn('gitapp checkout --detach "$EXPECTED"', self.text)
        self.assertIn("аддитивная схема могла остаться применённой", self.text)

    def test_roles_models_smoke_and_marker_are_verified(self):
        for token in (
            "manage setup_roles",
            "manage collectstatic --noinput",
            "ControllerLineAccess",
            "ChargeObligation",
            "use_controller_workspace",
            "https://trud-1.ru/admin/deployment-status/",
            'test "$status_commit" = "$TARGET"',
        ):
            self.assertIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
