#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

APP = Path("/opt/trud-1-site")
STATUS = Path("/var/lib/trud-1/deployment-status.json")
BASE = Path("/var/lib/system-maintenance/release")
INBOX = BASE / "inbox"
PROCESSING = BASE / "processing"
RESULTS = BASE / "results"
ARCHIVE = BASE / "archive"
LOCK = Path("/run/trud-release-request.lock")
DEPLOY_SCRIPT_SHA256 = "ffe9d8f5cc7eff10881b94ee3e09658222bbf1ac0fa2d91aee1b859b2ab81a23"
BACKUP_SCRIPT_SHA256 = "790be71b5d37332e542a424e8a2ca58bb24a732fec2f7991b2010a6dce70649b"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
RELEASE_ID_RE = re.compile(r"^release-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{6}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
FORBIDDEN_PATHS = (
    "backend/requirements.txt",
    ":(glob)backend/**/migrations/**",
    "backend/config/settings.py",
    ":(glob)backend/**/management/**",
    "ops/backup-trud-site.sh",
    "ops/trud-1-backup.service",
    "ops/trud-1-backup.timer",
)


class ReleaseError(RuntimeError):
    pass


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(argv, *, check=True, capture=False, env=None, stdin=None):
    result = subprocess.run(
        list(argv),
        check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=env,
        input=stdin,
    )
    if check and result.returncode:
        detail = ""
        if capture:
            detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace")[:500].strip()
        raise ReleaseError(f"command failed rc={result.returncode}: {argv[0]} {detail}".strip())
    return result


def gitapp(*args, capture=True):
    result = run(
        [
            "/usr/bin/runuser", "-u", "trudsite", "--",
            "/usr/bin/git", "-C", str(APP), *args,
        ],
        capture=capture,
    )
    return result.stdout.decode("utf-8", "replace").strip() if capture else ""


def read_marker() -> str:
    data = json.loads(STATUS.read_text(encoding="utf-8"))
    if data.get("project") != "trud-1":
        raise ReleaseError("unexpected deployment marker project")
    value = str(data.get("commit") or "")
    if not SHA_RE.fullmatch(value):
        raise ReleaseError("invalid deployment marker commit")
    return value


def _trusted_dir(path: Path, *, uid: int, gid: int | None = None, allow_group_read=False):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid:
        raise ReleaseError(f"untrusted directory: {path}")
    if gid is not None and info.st_gid != gid:
        raise ReleaseError(f"unexpected group: {path}")
    forbidden = 0o022 if allow_group_read else 0o077
    if info.st_mode & forbidden:
        raise ReleaseError(f"unsafe directory mode: {path}")


def validate_descriptor(data, *, expected_release_id=None):
    if not isinstance(data, dict):
        raise ReleaseError("descriptor must be a JSON object")
    required = {"schema", "release_id", "expected_sha", "target_sha", "branch"}
    if set(data) != required or data.get("schema") != 1:
        raise ReleaseError("invalid descriptor schema")
    release_id = str(data["release_id"])
    expected_sha = str(data["expected_sha"])
    target_sha = str(data["target_sha"])
    branch = str(data["branch"])
    if expected_release_id is not None and release_id != expected_release_id:
        raise ReleaseError("release id does not match filename")
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ReleaseError("invalid release id")
    if not SHA_RE.fullmatch(expected_sha) or not SHA_RE.fullmatch(target_sha):
        raise ReleaseError("invalid commit SHA")
    if expected_sha == target_sha:
        raise ReleaseError("target already equals expected")
    if not BRANCH_RE.fullmatch(branch) or ".." in branch or "@{" in branch or "//" in branch:
        raise ReleaseError("invalid branch")
    return {
        "release_id": release_id,
        "expected_sha": expected_sha,
        "target_sha": target_sha,
        "branch": branch,
    }


def claim_descriptor(chat_uid: int):
    pending = sorted(
        path for path in INBOX.iterdir()
        if path.name.startswith("release-") and path.suffix == ".json"
    )
    if len(pending) != 1:
        raise ReleaseError(f"expected exactly one pending release descriptor, found {len(pending)}")
    source = pending[0]
    release_id = source.stem
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ReleaseError("invalid descriptor filename")
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ReleaseError("descriptor is not a regular file")
    if info.st_uid != chat_uid or info.st_mode & 0o077:
        raise ReleaseError("descriptor owner/mode is unsafe")
    claimed = PROCESSING / source.name
    if claimed.exists():
        raise ReleaseError("processing descriptor already exists")
    os.replace(source, claimed)
    os.chown(claimed, 0, 0)
    os.chmod(claimed, 0o600)
    return claimed, release_id


def write_result(chat_gid: int, release_id: str, payload: dict):
    final = RESULTS / f"{release_id}.json"
    tmp = RESULTS / f".{release_id}.{os.getpid()}.tmp"
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(tmp, 0, chat_gid)
        os.chmod(tmp, 0o640)
        os.replace(tmp, final)
    finally:
        if tmp.exists():
            tmp.unlink()
    return final


def archive_descriptor(path: Path):
    if not path.exists():
        return
    final = ARCHIVE / path.name
    if final.exists():
        raise ReleaseError("archive descriptor already exists")
    os.replace(path, final)
    os.chown(final, 0, 0)
    os.chmod(final, 0o600)


def extract_git_file(target: str, repo_path: str, destination: Path, expected_sha256: str):
    result = run(
        [
            "/usr/bin/runuser", "-u", "trudsite", "--",
            "/usr/bin/git", "-C", str(APP), "show", f"{target}:{repo_path}",
        ],
        capture=True,
    )
    data = result.stdout
    if sha256_bytes(data) != expected_sha256:
        raise ReleaseError(f"checksum mismatch: {repo_path}")
    destination.write_bytes(data)
    os.chmod(destination, 0o700)


def live_head_or_none():
    try:
        return gitapp("rev-parse", "HEAD")
    except Exception:
        return None


def marker_or_none():
    try:
        return read_marker()
    except Exception:
        return None


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        print("root, no arguments required", file=sys.stderr)
        return 2

    chat = pwd.getpwnam("chatgpt-remote")
    _trusted_dir(BASE, uid=0, gid=chat.pw_gid, allow_group_read=True)
    _trusted_dir(INBOX, uid=chat.pw_uid, gid=chat.pw_gid)
    _trusted_dir(PROCESSING, uid=0)
    _trusted_dir(RESULTS, uid=0, gid=chat.pw_gid, allow_group_read=True)
    _trusted_dir(ARCHIVE, uid=0)

    lock_fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another Trud release request is active", file=sys.stderr)
            return 2

        claimed = None
        release_id = None
        descriptor = {}
        stage = "claim"
        stage_dir = None
        backup_root = None
        try:
            claimed, release_id = claim_descriptor(chat.pw_uid)
            raw = json.loads(claimed.read_text(encoding="utf-8"))
            descriptor = validate_descriptor(raw, expected_release_id=release_id)
            if (RESULTS / f"{release_id}.json").exists():
                raise ReleaseError("release result already exists")

            expected = descriptor["expected_sha"]
            target = descriptor["target_sha"]
            branch = descriptor["branch"]

            stage = "preflight"
            run(["/usr/bin/git", "check-ref-format", "--branch", branch])
            if read_marker() != expected:
                raise ReleaseError("production marker drift")
            if gitapp("rev-parse", "HEAD") != expected:
                raise ReleaseError("production Git head drift")
            if gitapp("status", "--porcelain"):
                raise ReleaseError("production tree is dirty")

            gitapp("fetch", "--no-tags", "origin", f"refs/heads/{branch}", capture=False)
            if gitapp("rev-parse", "FETCH_HEAD") != target:
                raise ReleaseError("fetched branch head does not equal target")
            gitapp("cat-file", "-e", f"{target}^{{commit}}")
            run(
                [
                    "/usr/bin/runuser", "-u", "trudsite", "--",
                    "/usr/bin/git", "-C", str(APP), "merge-base", "--is-ancestor", expected, target,
                ],
                capture=True,
            )

            forbidden = gitapp("diff", "--name-only", expected, target, "--", *FORBIDDEN_PATHS)
            if forbidden:
                raise ReleaseError("release contains migration/config/dependency-sensitive paths")

            stage_dir = Path(tempfile.mkdtemp(prefix="trud-selfservice.", dir="/var/backups"))
            deploy_script = stage_dir / "deploy-compatible.sh"
            backup_script = stage_dir / "backup-trud-site.sh"
            extract_git_file(target, "ops/deploy-compatible.sh", deploy_script, DEPLOY_SCRIPT_SHA256)
            extract_git_file(target, "ops/backup-trud-site.sh", backup_script, BACKUP_SCRIPT_SHA256)
            run(["/bin/bash", "-n", str(deploy_script), str(backup_script)])

            stage = "backup"
            backup_root = Path(tempfile.mkdtemp(prefix="trud-1-release-selfservice.", dir="/var/backups"))
            env = os.environ.copy()
            env["TRUD_BACKUP_ROOT"] = str(backup_root)
            run(["/bin/bash", str(backup_script)], env=env)

            stage = "deploy"
            deploy = run(
                ["/bin/bash", str(deploy_script), target, expected],
                check=False,
            )
            if deploy.returncode:
                raise ReleaseError(f"compatible deploy failed rc={deploy.returncode}")

            stage = "postcheck"
            if read_marker() != target:
                raise ReleaseError("post-deploy marker mismatch")
            if gitapp("rev-parse", "HEAD") != target:
                raise ReleaseError("post-deploy Git head mismatch")
            for unit in ("trud-1-site.service", "nginx.service", "postgresql@17-main.service"):
                run(["/usr/bin/systemctl", "is-active", "--quiet", unit])
            for url in (
                "https://trud-1.ru/",
                "https://trud-1.ru/admin/cabinet/login/",
                "https://trud-1.ru/admin/deployment-status/",
            ):
                run(["/usr/bin/curl", "--fail", "--connect-timeout", "3", "--max-time", "10", "-sS", "-o", "/dev/null", url])

            payload = {
                "schema": 1,
                "release_id": release_id,
                "status": "PASS",
                "stage": "complete",
                "expected_sha": expected,
                "target_sha": target,
                "branch": branch,
                "backup_root": str(backup_root),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            result = write_result(chat.pw_gid, release_id, payload)
            archive_descriptor(claimed)
            if stage_dir:
                shutil.rmtree(stage_dir, ignore_errors=True)
            print(f"TRUD_RELEASE=PASS")
            print(f"RELEASE_ID={release_id}")
            print(f"RESULT={result}")
            print(f"target={target}")
            return 0

        except Exception as exc:
            target = descriptor.get("target_sha")
            expected = descriptor.get("expected_sha")
            committed = bool(target and marker_or_none() == target and live_head_or_none() == target)
            status = "COMMITTED_POSTCHECK_FAILED" if committed else "FAILED"
            rc = 3 if committed else 1
            if release_id:
                payload = {
                    "schema": 1,
                    "release_id": release_id,
                    "status": status,
                    "stage": stage,
                    "expected_sha": expected,
                    "target_sha": target,
                    "branch": descriptor.get("branch"),
                    "backup_root": str(backup_root) if backup_root else None,
                    "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    result = write_result(chat.pw_gid, release_id, payload)
                    print(f"RESULT={result}", file=sys.stderr)
                except Exception as result_exc:
                    print(f"result write failed: {result_exc}", file=sys.stderr)
                try:
                    if claimed:
                        archive_descriptor(claimed)
                except Exception as archive_exc:
                    print(f"archive failed: {archive_exc}", file=sys.stderr)
            print(f"TRUD_RELEASE={status} stage={stage}: {exc}", file=sys.stderr)
            if stage_dir and not committed:
                print(f"diagnostic_stage={stage_dir}", file=sys.stderr)
            return rc


if __name__ == "__main__":
    raise SystemExit(main())
