"""Exercise the actual workflow Bash with synthetic curl responses, without network."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import textwrap
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / "workflows/production-status.yml"
PRODUCTION = "6fb32eff0f389eda944371d802cd14dec8eb2adb"
INTEGRATION = "617db3699536a1ab1f4391b88966b825a4ee5dd6"
DEPLOYED_AT = "2026-10-06T09:07:58Z"
VALID = {"project": "trud-1", "commit": PRODUCTION, "deployed_at": DEPLOYED_AT}


def workflow_steps():
    steps = {}
    for block in re.split(r"(?m)^      - ", WORKFLOW.read_text()):
        if block.startswith("name: ") and "\n        run: |\n" in block:
            name = block.splitlines()[0].removeprefix("name: ")
            steps[name] = textwrap.dedent(block.split("\n        run: |\n", 1)[1])
    return steps


class ProductionStatusTests(unittest.TestCase):
    def exercise(self, expected="", production=None, production_exit=0):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "production.json").write_text(
                json.dumps(VALID) if production is None else production
            )
            (root / "summary").touch()
            (root / "output").touch()
            curl = root / "curl"
            curl.write_text("""#!/bin/bash
set -eu
url=${!#}
printf '%s\\n' "$url" >> "$FIXTURE/requests"
if [ "$url" != 'https://trud-1.ru/admin/deployment-status/' ]; then
  echo 'Unexpected network request' >&2; exit 99
fi
if [ "$PRODUCTION_EXIT" != 0 ]; then exit "$PRODUCTION_EXIT"; fi
cat "$FIXTURE/production.json"
""")
            curl.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "FIXTURE": str(root),
                "PRODUCTION_EXIT": str(production_exit),
                "EXPECTED_PRODUCTION_COMMIT": expected,
                "GITHUB_STEP_SUMMARY": str(root / "summary"),
                "GITHUB_OUTPUT": str(root / "output"),
            }
            scripts = workflow_steps()
            results = []
            for name in (
                "Probe and validate production marker",
                "Compare with approved production release",
            ):
                result = subprocess.run(
                    ["bash", "-e", "-o", "pipefail", "-c", scripts[name]],
                    env=env, text=True, capture_output=True, timeout=5,
                )
                results.append(result)
                if result.returncode:
                    break
                outputs = dict(
                    line.split("=", 1)
                    for line in (root / "output").read_text().splitlines()
                )
                env["PRODUCTION_COMMIT"] = outputs["commit"]
            return (
                results,
                (root / "summary").read_text(),
                (root / "output").read_text(),
                (root / "requests").read_text().splitlines(),
            )

    def assert_marker_ok(self, results, summary, output, requests):
        self.assertEqual(results[0].returncode, 0, results[0].stderr)
        self.assertIn("MARKER_STATUS=OK", results[0].stdout)
        self.assertIn("PRODUCTION_COMMIT=" + PRODUCTION, results[0].stdout)
        self.assertIn("CHECKED_AT=", results[0].stdout)
        self.assertIn(PRODUCTION, summary)
        self.assertIn(DEPLOYED_AT, summary)
        self.assertIn("application acceptance is separate", summary)
        self.assertEqual(output, "commit=" + PRODUCTION + "\n")
        self.assertEqual(requests, ["https://trud-1.ru/admin/deployment-status/"])

    def test_approved_release_match(self):
        values = self.exercise(expected=PRODUCTION)
        self.assert_marker_ok(*values)
        self.assertEqual(values[0][1].returncode, 0)
        self.assertIn("RELEASE_STATUS=MATCH", values[0][1].stdout)

    def test_no_baseline_is_unknown_not_an_integration_failure(self):
        values = self.exercise()
        self.assert_marker_ok(*values)
        self.assertEqual(values[0][1].returncode, 0)
        self.assertIn("RELEASE_STATUS=UNKNOWN", values[0][1].stdout)
        self.assertIn("::warning::", values[0][1].stdout)
        self.assertNotIn(INTEGRATION, values[1])
        source = WORKFLOW.read_text()
        self.assertNotIn("/branches/", source)
        self.assertNotIn("GH_TOKEN", source)

    def test_mismatch_with_explicit_approved_release_fails(self):
        values = self.exercise(expected="a" * 40)
        self.assert_marker_ok(*values)
        self.assertEqual(values[0][1].returncode, 1)
        self.assertIn("RELEASE_STATUS=MISMATCH", values[0][1].stdout)
        self.assertIn("::error::", values[0][1].stdout)

    def test_invalid_baseline_is_configuration_error(self):
        for expected in ("bad", "A" * 40, "a" * 39, "a" * 40 + "\nINJECTED=true"):
            with self.subTest(expected=expected):
                values = self.exercise(expected=expected)
                self.assert_marker_ok(*values)
                self.assertEqual(values[0][1].returncode, 1)
                self.assertIn("RELEASE_STATUS=INVALID_BASELINE", values[0][1].stdout)
                self.assertNotIn("INJECTED", values[1])

    def test_http_network_and_timeout_errors_do_not_claim_site_outage(self):
        for code in (22, 7, 28):
            with self.subTest(curl_exit=code):
                results, summary, output, requests = self.exercise(production_exit=code)
                self.assertEqual([r.returncode for r in results], [code])
                self.assertIn("MARKER_STATUS=UNKNOWN", results[0].stdout)
                self.assertIn("not proof of a site outage", summary)
                self.assertEqual(output, "")
                self.assertEqual(len(requests), 1)

    def test_invalid_marker_fails_before_release_comparison(self):
        bad = ["not json", "[]", "null", json.dumps({**VALID, "project": "other"})]
        for field, values in (
            ("commit", ("", None, 123, "a" * 39, "g" * 40, "a" * 40 + "\n")),
            ("deployed_at", ("", None, 123, "not-a-date", "2026-02-30T00:00:00Z",
                             "2026-10-06", "2026-10-06T09:07:58",
                             "2026-10-06\n09:07:58Z")),
        ):
            bad.append(json.dumps({k: v for k, v in VALID.items() if k != field}))
            bad.extend(json.dumps({**VALID, field: v}) for v in values)
        for payload in bad:
            with self.subTest(payload=payload):
                results, summary, output, requests = self.exercise(production=payload)
                self.assertEqual([r.returncode for r in results], [1])
                self.assertIn("MARKER_STATUS=INVALID", results[0].stdout)
                self.assertNotIn("RELEASE_STATUS=", results[0].stdout)
                self.assertIn("Marker: **INVALID**", summary)
                self.assertEqual(output, "")
                self.assertEqual(len(requests), 1)

    def test_pr_only_runs_offline_tests(self):
        source = WORKFLOW.read_text()
        self.assertEqual(source.count("if: github.event_name != 'pull_request'"), 2)
        self.assertIn("pull_request:\n    branches: [main]", source)
        self.assertIn("Test production status logic offline", source)

    def test_bash_syntax(self):
        for name, script in workflow_steps().items():
            with self.subTest(step=name):
                result = subprocess.run(
                    ["bash", "-n"], input=script, text=True, capture_output=True
                )
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
