#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/var/lib/system-maintenance/release")
INBOX = BASE / "inbox"
RESULTS = BASE / "results"
SUBMIT = Path("/usr/local/bin/system-maintenance-submit")
LOCK = INBOX / ".release-submit.lock"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


def die(message, code=2):
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def validate(expected_sha: str, target_sha: str, branch: str):
    if not SHA_RE.fullmatch(expected_sha) or not SHA_RE.fullmatch(target_sha):
        die("expected and target must be full lowercase 40-hex Git SHAs")
    if expected_sha == target_sha:
        die("target already equals expected")
    if not BRANCH_RE.fullmatch(branch) or ".." in branch or "@{" in branch or "//" in branch:
        die("invalid branch")
    return expected_sha, target_sha, branch


def _write_descriptor(path: Path, payload: dict):
    temporary = path.with_name("." + path.name + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        dir_fd = os.open(INBOX, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def main():
    if os.geteuid() == 0:
        die("run as the ordinary authorized release submitter, not root")
    if len(sys.argv) != 4:
        die("usage: trud-release-submit EXPECTED_SHA TARGET_SHA BRANCH")

    expected, target, branch = validate(sys.argv[1], sys.argv[2], sys.argv[3])
    if not SUBMIT.is_file() or not os.access(SUBMIT, os.X_OK):
        die("system-maintenance submit client is unavailable")

    info = INBOX.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        die("release inbox owner/mode is unsafe")

    lock_fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            die("another release submission is in progress")

        pending = [
            path for path in INBOX.iterdir()
            if path.name.startswith("release-") and path.suffix == ".json"
        ]
        if pending:
            die("a release descriptor is already pending")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        release_id = f"release-{stamp}-{target[:12]}-{secrets.token_hex(3)}"
        descriptor = INBOX / f"{release_id}.json"
        payload = {
            "schema": 1,
            "release_id": release_id,
            "expected_sha": expected,
            "target_sha": target,
            "branch": branch,
        }
        _write_descriptor(descriptor, payload)

        result = subprocess.run(
            [str(SUBMIT), "deploy-trud-compatible"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            if descriptor.exists():
                descriptor.unlink()
                print("RELEASE_DESCRIPTOR_REMOVED=yes", file=sys.stderr)
            else:
                print(
                    "RELEASE_SUBMIT=UNCERTAIN descriptor already claimed; reconcile results before retry",
                    file=sys.stderr,
                )
            if result.stderr:
                print(result.stderr.strip(), file=sys.stderr)
            raise SystemExit(result.returncode)

        request_id = None
        for line in result.stdout.splitlines():
            if line.startswith("REQUEST_ID="):
                request_id = line.split("=", 1)[1].strip()
                break
        if not request_id:
            print(
                "RELEASE_SUBMIT=UNCERTAIN maintenance request accepted without parseable REQUEST_ID",
                file=sys.stderr,
            )
            raise SystemExit(3)

        print(f"RELEASE_ID={release_id}")
        print(f"REQUEST_ID={request_id}")
        print(f"MAINTENANCE_RESULT=/var/lib/system-maintenance/results/{request_id}.json")
        print(f"RELEASE_RESULT={RESULTS / (release_id + '.json')}")
        print("STATE=SUBMITTED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
