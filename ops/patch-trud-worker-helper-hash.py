#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import os
import stat
import sys
from pathlib import Path

HELPER_PATH = "/usr/local/sbin/deploy-trud-compatible"


class WorkerContractError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_name(node, name):
    return isinstance(node, ast.Name) and node.id == name


def _helper_function(tree):
    functions = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "launch_trud_deploy"
    ]
    if len(functions) != 1 or not isinstance(functions[0], ast.FunctionDef):
        raise WorkerContractError("expected exactly one synchronous launch_trud_deploy")
    function = functions[0]
    args = function.args
    if args.posonlyargs or args.args or args.kwonlyargs or args.vararg or args.kwarg:
        raise WorkerContractError("launch_trud_deploy must take no arguments")
    return function


def inspect_worker(worker_text: str) -> str:
    try:
        tree = ast.parse(worker_text)
    except SyntaxError as error:
        raise WorkerContractError("worker is not valid Python") from error
    function = _helper_function(tree)

    helper_assignments = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not _is_name(node.targets[0], "helper"):
            continue
        value = node.value
        if (
            isinstance(value, ast.Call)
            and _is_name(value.func, "Path")
            and len(value.args) == 1
            and isinstance(value.args[0], ast.Constant)
            and value.args[0].value == HELPER_PATH
        ):
            helper_assignments.append(node)
    if len(helper_assignments) != 1:
        raise WorkerContractError("fixed deploy helper assignment is missing or ambiguous")

    checksum_values = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1 or len(node.comparators) != 1:
            continue
        left = node.left
        comparator = node.comparators[0]
        if (
            isinstance(left, ast.Call)
            and _is_name(left.func, "sha256")
            and len(left.args) == 1
            and _is_name(left.args[0], "helper")
            and isinstance(comparator, ast.Constant)
            and isinstance(comparator.value, str)
            and len(comparator.value) == 64
            and all(char in "0123456789abcdef" for char in comparator.value)
        ):
            checksum_values.append(comparator.value)
    if len(checksum_values) != 1:
        raise WorkerContractError("fixed helper checksum guard is missing or ambiguous")

    launches = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not _is_name(node.func, "run") or not node.args:
            continue
        argv = node.args[0]
        if not isinstance(argv, (ast.List, ast.Tuple)):
            continue
        elements = list(argv.elts)
        has_systemd_run = any(
            isinstance(item, ast.Constant) and item.value == "systemd-run"
            for item in elements
        )
        has_helper = any(
            isinstance(item, ast.Call)
            and _is_name(item.func, "str")
            and len(item.args) == 1
            and _is_name(item.args[0], "helper")
            for item in elements
        )
        if has_systemd_run and has_helper:
            launches.append(node)
    if len(launches) != 1:
        raise WorkerContractError("fixed systemd-run helper launch is missing or ambiguous")

    return checksum_values[0]


def patch_worker(worker_text: str, current_helper_sha: str, new_helper_sha: str) -> str:
    pinned = inspect_worker(worker_text)
    if pinned != current_helper_sha:
        raise WorkerContractError("installed worker checksum pin does not match installed helper")
    if len(new_helper_sha) != 64 or any(char not in "0123456789abcdef" for char in new_helper_sha):
        raise WorkerContractError("invalid new helper checksum")
    if current_helper_sha == new_helper_sha:
        return worker_text

    tree = ast.parse(worker_text)
    function = _helper_function(tree)
    lines = worker_text.splitlines(keepends=True)
    start = function.lineno - 1
    end = function.end_lineno
    segment = "".join(lines[start:end])
    if segment.count(current_helper_sha) != 1:
        raise WorkerContractError("current helper checksum occurrence is not unique in launcher")
    changed_segment = segment.replace(current_helper_sha, new_helper_sha, 1)
    patched = "".join(lines[:start]) + changed_segment + "".join(lines[end:])
    compile(patched, "system-maintenance-worker", "exec")
    if inspect_worker(patched) != new_helper_sha:
        raise WorkerContractError("patched worker contract did not validate")
    return patched


def _trusted_regular(path: Path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise WorkerContractError(f"untrusted root file: {path}")


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--check":
        worker = Path(sys.argv[2])
        helper = Path(sys.argv[3])
        _trusted_regular(worker)
        _trusted_regular(helper)
        pinned = inspect_worker(worker.read_text(encoding="utf-8"))
        if pinned != file_sha256(helper):
            raise WorkerContractError("worker/helper checksum contract mismatch")
        print("WORKER_HELPER_PIN=PASS")
        return 0

    if len(sys.argv) != 5:
        print(
            "usage: patch-trud-worker-helper-hash.py WORKER CURRENT_HELPER NEW_HELPER OUTPUT\n"
            "   or: patch-trud-worker-helper-hash.py --check WORKER HELPER",
            file=sys.stderr,
        )
        return 2

    worker, current_helper, new_helper, output = map(Path, sys.argv[1:])
    for path in (worker, current_helper, new_helper):
        _trusted_regular(path)
    worker_text = worker.read_text(encoding="utf-8")
    patched = patch_worker(
        worker_text,
        file_sha256(current_helper),
        file_sha256(new_helper),
    )
    output.write_text(patched, encoding="utf-8")
    os.chmod(output, 0o600)
    print("WORKER_HELPER_PIN_PATCH=PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except WorkerContractError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
