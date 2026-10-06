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
PROMOTION_REF = "refs/heads/release/production-compatible"
DEPLOY_SCRIPT_SHA256 = "ffe9d8f5cc7eff10881b94ee3e09658222bbf1ac0fa2d91aee1b859b2ab81a23"
BACKUP_SCRIPT_SHA256 = "790be71b5d37332e542a424e8a2ca58bb24a732fec2f7991b2010a6dce70649b"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REQUEST_RE = re.compile(r"^request-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}$")
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


def run(argv, *, check=True, capture=False, env=None):
    result = subprocess.run(
        list(argv),
        check=False,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=env,
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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def marker_or_none():
    try:
        data = json.loads(STATUS.read_text(encoding="utf-8"))
        value = str(data.get("commit") or "")
        if data.get("project") != "trud-1" or not SHA_RE.fullmatch(value):
            return None
        return value
    except Exception:
        return None


def head_or_none():
    try:
        return gitapp("rev-parse", "HEAD")
    except Exception:
        return None


def validate_request(data, expected_request_id=None):
    if not isinstance(data, dict) or set(data) != {"schema", "request_id"}:
        raise ReleaseError("invalid request schema")
    if data.get("schema") != 1:
        raise ReleaseError("unsupported request schema")
    request_id = str(data.get("request_id") or "")
    if not REQUEST_RE.fullmatch(request_id):
        raise ReleaseError("invalid request id")
    if expected_request_id is not None and request_id != expected_request_id:
        raise ReleaseError("request id does not match filename")
    return request_id


def trusted_dir(path: Path, *, uid: int, gid: int | None = None, group_read=False):
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid:
        raise ReleaseError(f"untrusted directory: {path}")
    if gid is not None and info.st_gid != gid:
        raise ReleaseError(f"unexpected group: {path}")
    forbidden = 0o027 if group_read else 0o077
    if info.st_mode & forbidden:
        raise ReleaseError(f"unsafe directory mode: {path}")


def claim_request(chat_uid: int):
    pending = sorted(
        path for path in INBOX.iterdir()
        if path.name.startswith("request-") and path.suffix == ".json"
    )
    if len(pending) != 1:
        raise ReleaseError(f"expected exactly one pending request, found {len(pending)}")
    source = pending[0]
    request_id = source.stem
    if not REQUEST_RE.fullmatch(request_id):
        raise ReleaseError("invalid request filename")
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ReleaseError("request is not a regular file")
    if info.st_uid != chat_uid or info.st_mode & 0o077:
        raise ReleaseError("request owner/mode is unsafe")
    claimed = PROCESSING / source.name
    if claimed.exists():
        raise ReleaseError("processing request already exists")
    os.replace(source, claimed)
    os.chown(claimed, 0, 0)
    os.chmod(claimed, 0o600)
    data = json.loads(claimed.read_text(encoding="utf-8"))
    validate_request(data, expected_request_id=request_id)
    return claimed, request_id


def write_result(chat_gid: int, request_id: str, payload: dict):
    final = RESULTS / f"{request_id}.json"
    tmp = RESULTS / f".{request_id}.{os.getpid()}.tmp"
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


def archive_request(path: Path):
    if not path.exists():
        return
    final = ARCHIVE / path.name
    if final.exists():
        raise ReleaseError("archive request already exists")
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


def main():
    if os.geteuid() != 0 or len(sys.argv) != 1:
        print("root, no arguments required", file=sys.stderr)
        return 2

    chat = pwd.getpwnam("chatgpt-remote")
    trusted_dir(BASE, uid=0, gid=chat.pw_gid, group_read=True)
    trusted_dir(INBOX, uid=chat.pw_uid, gid=chat.pw_gid)
    trusted_dir(PROCESSING, uid=0)
    trusted_dir(RESULTS, uid=0, gid=chat.pw_gid, group_read=True)
    trusted_dir(ARCHIVE, uid=0)

    lock_fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another Trud release request is active", file=sys.stderr)
            return 2

        claimed = None
        request_id = None
        expected = None
        target = None
        stage = "claim"
        stage_dir = None
        backup_root = None
        try:
            claimed, request_id = claim_request(chat.pw_uid)

            stage = "preflight"
            expected = marker_or_none()
            if expected is None:
                raise ReleaseError("invalid production marker")
            if head_or_none() != expected:
                raise ReleaseError("production Git head/marker drift")
            if gitapp("status", "--porcelain"):
                raise ReleaseError("production tree is dirty")

            gitapp("fetch", "--no-tags", "origin", PROMOTION_REF, capture=False)
            target = gitapp("rev-parse", "FETCH_HEAD")
            if not SHA_RE.fullmatch(target):
                raise ReleaseError("invalid promoted target SHA")

            if target == expected:
                payload = {
                    "schema": 1,
                    "request_id": request_id,
                    "status": "NOOP_ALREADY_CURRENT",
                    "stage": "complete",
                    "promotion_ref": PROMOTION_REF,
                    "expected_sha": expected,
                    "target_sha": target,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
                result = write_result(chat.pw_gid, request_id, payload)
                archive_request(claimed)
                print("TRUD_RELEASE=NOOP_ALREADY_CURRENT")
                print(f"REQUEST_ID={request_id}")
                print(f"RESULT={result}")
                return 0

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
                raise ReleaseError("promoted release contains migration/config/dependency-sensitive paths")

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
            deploy = run(["/bin/bash", str(deploy_script), target, expected], check=False)
            if deploy.returncode:
                raise ReleaseError(f"compatible deploy failed rc={deploy.returncode}")

            stage = "postcheck"
            if marker_or_none() != target or head_or_none() != target:
                raise ReleaseError("post-deploy marker/Git mismatch")
            for unit in ("trud-1-site.service", "nginx.service", "postgresql@17-main.service"):
                run(["/usr/bin/systemctl", "is-active", "--quiet", unit])
            for url in (
                "https://trud-1.ru/",
                "https://trud-1.ru/admin/cabinet/login/",
                "https://trud-1.ru/admin/deployment-status/",
            ):
                run([
                    "/usr/bin/curl", "--fail", "--connect-timeout", "3",
                    "--max-time", "10", "-sS", "-o", "/dev/null", url,
                ])

            payload = {
                "schema": 1,
                "request_id": request_id,
                "status": "PASS",
                "stage": "complete",
                "promotion_ref": PROMOTION_REF,
                "expected_sha": expected,
                "target_sha": target,
                "backup_root": str(backup_root),
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
            result = write_result(chat.pw_gid, request_id, payload)
            archive_request(claimed)
            if stage_dir:
                shutil.rmtree(stage_dir, ignore_errors=True)
            print("TRUD_RELEASE=PASS")
            print(f"REQUEST_ID={request_id}")
            print(f"RESULT={result}")
            print(f"target={target}")
            return 0

        except Exception as exc:
            committed = bool(target and marker_or_none() == target and head_or_none() == target)
            status = "COMMITTED_POSTCHECK_FAILED" if committed else "FAILED"
            rc = 3 if committed else 1
            if request_id:
                payload = {
                    "schema": 1,
                    "request_id": request_id,
                    "status": status,
                    "stage": stage,
                    "promotion_ref": PROMOTION_REF,
                    "expected_sha": expected,
                    "target_sha": target,
                    "backup_root": str(backup_root) if backup_root else None,
                    "error": f"{type(exc).__name__}: {str(exc)[:400]}",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    result = write_result(chat.pw_gid, request_id, payload)
                    print(f"RESULT={result}", file=sys.stderr)
                except Exception as result_exc:
                    print(f"result write failed: {result_exc}", file=sys.stderr)
                try:
                    if claimed:
                        archive_request(claimed)
                except Exception as archive_exc:
                    print(f"archive failed: {archive_exc}", file=sys.stderr)
            print(f"TRUD_RELEASE={status} stage={stage}: {exc}", file=sys.stderr)
            if stage_dir and not committed:
                print(f"diagnostic_stage={stage_dir}", file=sys.stderr)
            return rc


if __name__ == "__main__":
    raise SystemExit(main())
