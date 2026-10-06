"""Contract tests for the one-time exact #160 production launcher."""
from pathlib import Path
import subprocess
import unittest

OPS = Path(__file__).resolve().parents[1]
SCRIPT = OPS / "release-workbench-160.sh"


class ReleaseWorkbench160ContractTests(unittest.TestCase):
    def setUp(self):
        self.text = SCRIPT.read_text()

    def test_bash_syntax(self):
        subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    def test_exact_target_and_baseline_are_pinned(self):
        self.assertIn("EXPECTED=7e685dfa6a331a433e916a62b1d555fe04847c4c", self.text)
        self.assertIn("TARGET=6fb32eff0f389eda944371d802cd14dec8eb2adb", self.text)
        self.assertIn("BRANCH=fix/workbench-contrast-regression-20261006", self.text)
        self.assertIn("[[ $(id -u) -eq 0 && $# -eq 0 ]]", self.text)

    def test_diff_whitelist_is_only_the_reviewed_three_files(self):
        start = self.text.index("EXPECTED_DIFF=$(cat <<'EOF'") + len("EXPECTED_DIFF=$(cat <<'EOF'\n")
        end = self.text.index("\nEOF\n)", start)
        self.assertEqual(
            self.text[start:end].splitlines(),
            [
                "backend/water/staff_workspace_access_e2e_tests.py",
                "backend/water/static/water/workbench.css",
                "backend/water/templates/water/work/workbench.html",
            ],
        )
        self.assertIn("backend/requirements.txt backend/config/settings.py", self.text)
        self.assertIn("backend/**/migrations/**", self.text)
        self.assertIn("backend/**/management/**", self.text)

    def test_exact_artifact_hashes_and_visual_fix_are_pinned(self):
        self.assertIn(
            "DEPLOY_SHA256=ffe9d8f5cc7eff10881b94ee3e09658222bbf1ac0fa2d91aee1b859b2ab81a23",
            self.text,
        )
        self.assertIn(
            "BACKUP_SHA256=790be71b5d37332e542a424e8a2ca58bb24a732fec2f7991b2010a6dce70649b",
            self.text,
        )
        self.assertIn(
            "CSS_SHA256=03ef8259e570c80b91dd97034169e64e34ba8b8a0ead9224cfb3425e8dfbacfa",
            self.text,
        )
        self.assertIn(".wb a.ws-primary{color:#fff;text-decoration:none}", self.text)

    def test_restore_verified_backup_precedes_deploy_and_postchecks_follow(self):
        backup = self.text.index('TRUD_BACKUP_ROOT="$BACKUP_ROOT" bash "$STAGE/backup-trud-site.sh"')
        deploy = self.text.index('bash "$STAGE/deploy-compatible.sh" "$TARGET" "$EXPECTED"')
        marker = self.text.index("Installed marker mismatch")
        static_bytes = self.text.index("admin-static/water/workbench.css?release=$TARGET")
        passed = self.text.index("RELEASE_160=PASS")
        self.assertLess(backup, deploy)
        self.assertLess(deploy, marker)
        self.assertLess(marker, static_bytes)
        self.assertLess(static_bytes, passed)

    def test_no_destructive_cleanup_or_unpinned_target_arguments(self):
        self.assertNotIn("rm -rf", self.text)
        self.assertNotIn("git reset", self.text)
        self.assertNotIn("git clean", self.text)
        self.assertNotIn("sudo ", self.text)
        self.assertNotIn("TARGET=" + "$" + "{1", self.text)
        self.assertIn("never overwritten or deleted", self.text)


if __name__ == "__main__":
    unittest.main()
