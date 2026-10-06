"""Repository guard against production-derived privileged maintenance fixtures."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "ops" / "tests"


class RepositoryHygieneTests(unittest.TestCase):
    def test_maintenance_worker_fixtures_are_explicitly_synthetic(self):
        offenders = sorted(
            path.relative_to(ROOT).as_posix()
            for path in TESTS.glob("*maintenance-worker*.fixture")
            if "synthetic" not in path.name.lower()
        )
        self.assertEqual(
            offenders,
            [],
            "Production-derived maintenance snapshots must never be committed; "
            "use explicitly synthetic contract fixtures only: " + ", ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
