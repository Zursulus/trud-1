from pathlib import Path
import subprocess
import unittest

OPS = Path(__file__).resolve().parents[1]
SCRIPT = OPS / "release-workbench-contrast-160.sh"

class WorkbenchContrastReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding="utf-8")

    def test_shell_syntax(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exact_baseline_target_branch_and_three_file_scope(self):
        for token in (
            "EXPECTED=7e685dfa6a331a433e916a62b1d555fe04847c4c",
            "TARGET=6fb32eff0f389eda944371d802cd14dec8eb2adb",
            "BRANCH=fix/workbench-contrast-regression-20261006",
            "backend/water/staff_workspace_access_e2e_tests.py",
            "backend/water/static/water/workbench.css",
            "backend/water/templates/water/work/workbench.html",
            '[[ "$ACTUAL_FILES" == "$EXPECTED_FILES" ]]',
        ):
            self.assertIn(token, self.text)

    def test_fetch_is_pinned_and_live_drift_fails_closed(self):
        for token in (
            '[[ $(gitapp rev-parse HEAD) == "$EXPECTED" ]]',
            '[[ -z $(gitapp status --porcelain) ]]',
            'gitapp fetch --no-tags origin "refs/heads/$BRANCH"',
            '[[ $(gitapp rev-parse FETCH_HEAD) == "$TARGET" ]]',
            'gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"',
            "Production drift",
        ):
            self.assertIn(token, self.text)

    def test_generic_helper_and_backup_are_checksum_pinned(self):
        self.assertIn("ffe9d8f5cc7eff10881b94ee3e09658222bbf1ac0fa2d91aee1b859b2ab81a23", self.text)
        self.assertIn("790be71b5d37332e542a424e8a2ca58bb24a732fec2f7991b2010a6dce70649b", self.text)
        self.assertIn('gitapp show "$TARGET:ops/deploy-compatible.sh"', self.text)
        self.assertIn('gitapp show "$TARGET:ops/backup-trud-site.sh"', self.text)
        self.assertNotIn("deploy-migration-aware.sh", self.text)

    def test_verified_backup_precedes_deploy(self):
        backup = self.text.index('TRUD_BACKUP_ROOT="$BACKUP_ROOT" bash "$STAGE/backup-trud-site.sh"')
        deploy = self.text.index('bash "$STAGE/deploy-compatible.sh" "$TARGET" "$EXPECTED"')
        self.assertLess(backup, deploy)
        self.assertIn("VERIFIED_BACKUP_ROOT=", self.text)

    def test_postcheck_requires_marker_services_and_exact_collected_css(self):
        deploy = self.text.index('bash "$STAGE/deploy-compatible.sh" "$TARGET" "$EXPECTED"')
        marker = self.text.index("assert marker.get('commit') == sys.argv[2]", deploy)
        css = self.text.index('cmp "$CSS_EXPECTED" "$APP/backend/staticfiles/water/workbench.css"', deploy)
        http = self.text.index('admin-static/water/workbench.css?release=$TARGET', deploy)
        self.assertLess(deploy, marker)
        self.assertLess(marker, css)
        self.assertLess(css, http)
        self.assertIn("03ef8259e570c80b91dd97034169e64e34ba8b8a0ead9224cfb3425e8dfbacfa", self.text)

    def test_late_postcheck_failure_is_committed_not_fake_rollback(self):
        deploy = self.text.index('bash "$STAGE/deploy-compatible.sh" "$TARGET" "$EXPECTED"')
        late = self.text.index("RELEASE_160=COMMITTED_POSTCHECK_FAILED", deploy)
        self.assertLess(deploy, late)
        self.assertIn("exit 3", self.text)
        self.assertIn("RELEASE_160=PASS", self.text)

    def test_no_generic_privilege_or_destructive_shortcuts(self):
        for forbidden in ("sudo ", "eval ", "reset --hard", "force-push", "rm -rf /var/backups"):
            self.assertNotIn(forbidden, self.text)

if __name__ == "__main__":
    unittest.main()
