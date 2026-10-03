from pathlib import Path
import subprocess
import unittest


OPS = Path(__file__).resolve().parents[1]
WRAPPER = OPS / "deploy-trud-compatible.sh"
RELEASE = OPS / "deploy-migration-aware.sh"


class Zur143MigrationReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wrapper = WRAPPER.read_text(encoding="utf-8")
        cls.release = RELEASE.read_text(encoding="utf-8")

    def test_shell_syntax(self):
        for script in (WRAPPER, RELEASE):
            with self.subTest(script=script.name):
                result = subprocess.run(
                    ["bash", "-n", str(script)], capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_root_manifest_makes_migration_intent_explicit(self):
        for token in (
            'RELEASE_KIND',
            'MIGRATION_INTENT',
            'SETUP_ROLES_INTENT',
            'SCANNER_INTENT',
            'security-alert-0034',
            'water:0034_security_alert',
            'required-before-start',
        ):
            self.assertIn(token, self.wrapper)
    def test_wrapper_keeps_exact_target_branch_and_checksum_guards(self):
        for token in (
            'EXPECTED_LIVE_SHA',
            'TARGET_SHA',
            'BRANCH',
            'DEPLOY_SCRIPT_SHA256',
            'gitapp merge-base --is-ancestor',
            'sha256sum -c -',
            'ops/deploy-migration-aware.sh',
        ):
            self.assertIn(token, self.wrapper)
        for forbidden in ("eval ", "sudo ", "reset --hard", "force-push"):
            self.assertNotIn(forbidden, self.wrapper)

    def test_sensitive_release_delta_is_exact(self):
        for path in (
            "backend/config/settings.py",
            "backend/water/management/commands/setup_roles.py",
            "backend/water/management/commands/vtb_registry_dryrun.py",
            "backend/water/migrations/0034_security_alert.py",
        ):
            self.assertIn(path, self.release)
        for guarded in (
            "backend/requirements.txt",
            "ops/backup-trud-site.sh",
            "ops/trud-1-backup.service",
            "ops/trud-1-backup.timer",
        ):
            self.assertIn(guarded, self.release)
        self.assertIn("ACTUAL_SENSITIVE", self.release)
        self.assertIn("EXPECTED_SENSITIVE", self.release)

    def test_verified_backup_precedes_code_switch(self):
        self.assertIn("pg_dump -Fc trud_site", self.release)
        self.assertIn("pg_restore --list", self.release)
        self.assertIn("private-data.tar.gz", self.release)
        self.assertLess(
            self.release.index("pg_dump -Fc trud_site"),
            self.release.index('gitapp checkout --detach "$TARGET"'),
        )
    def test_release_order_is_migration_roles_scanner_then_start(self):
        migration = self.release.index("manage migrate --noinput")
        roles = self.release.index("manage setup_roles", migration)
        scanner = self.release.index("scanner_ready", roles)
        start = self.release.index("systemctl start trud-1-site.service", scanner)
        self.assertLess(migration, roles)
        self.assertLess(roles, scanner)
        self.assertLess(scanner, start)
        self.assertIn("[X] 0034_security_alert", self.release)

    def test_rollback_is_truthful_for_additive_schema(self):
        self.assertIn('gitapp checkout --detach "$EXPECTED"', self.release)
        self.assertIn("аддитивная схема 0034 могла остаться применённой", self.release)
        self.assertNotIn("migrate water 0033", self.release)
        self.assertIn("Never auto-drop", self.release)

    def test_late_postcheck_failure_is_committed_not_fake_rollback(self):
        commit_point = self.release.index("mv -f \"$status_tmp\" \"$STATUS\"")
        trap_clear = self.release.index("trap - EXIT INT TERM", commit_point)
        postcheck = self.release.index("deployment-status/", trap_clear)
        self.assertLess(commit_point, trap_clear)
        self.assertLess(trap_clear, postcheck)
        self.assertIn("DEPLOY_MIGRATION_AWARE=COMMITTED_POSTCHECK_FAILED", self.release)
        self.assertIn("exit 3", self.release)
        self.assertIn("DEPLOY_TRUD_COMPATIBLE=COMMITTED_POSTCHECK_FAILED", self.wrapper)
        self.assertIn('local_after', self.wrapper)


if __name__ == "__main__":
    unittest.main()
