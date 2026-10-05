#!/bin/bash
# One-time root bootstrap of a fixed action; never performs the site release.
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat

WORKER=/usr/local/sbin/system-maintenance-worker
HELPER=/usr/local/sbin/deploy-trud-compatible
SUBMIT=/usr/local/bin/system-maintenance-submit
MANIFEST=/etc/system-maintenance/trud-release.conf
INBOX=/var/lib/system-maintenance/inbox
SRC="$(cd "$(dirname "$0")" && pwd)"
[[ $(id -u) -eq 0 && $# -eq 0 ]] || { echo 'root, no arguments required' >&2; exit 2; }

exec 9>/run/trud-backup.lock
flock -n 9 || { echo 'backup/deploy already running' >&2; exit 2; }
# Preserve the old watcher state and refuse a broken/unknown installation.
systemctl is-active --quiet system-maintenance.path
systemctl is-active --quiet system-maintenance.service && {
    echo 'maintenance worker is active; wait for completion' >&2; exit 2;
}
if compgen -G "$INBOX/*.json" >/dev/null; then
    echo 'maintenance inbox is not empty; wait for reconciliation' >&2; exit 2
fi

STAGE=$(mktemp -d /var/backups/trud-broker-bootstrap.XXXXXXXX)
mkdir "$STAGE/before" "$STAGE/after"
python3 - "$WORKER" "$SUBMIT" "$HELPER" "$MANIFEST" "$SRC" "$STAGE" <<'PY'
import ast, pathlib, shutil, stat, sys
worker, submit, helper, manifest, src, stage = map(pathlib.Path, sys.argv[1:])
for path in (worker, submit, helper, manifest,
             src / 'deploy-trud-compatible.sh', src / 'trud-release-157.conf'):
    # Parent directories must exist and be trusted; no path/symlink surprises.
    for parent in (path.parent, *path.parent.parents):
        if parent == manifest.parent and not parent.exists() and not parent.is_symlink():
            (stage / 'before' / 'manifest-parent.absent').touch()
            continue
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise SystemExit(f'Untrusted parent: {parent}')
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise SystemExit(f'Untrusted installed file: {path}')
        if path in (worker, submit, helper, manifest):
            shutil.copy2(path, stage / 'before' / path.name)
    elif path in (worker, submit, helper, manifest):
        (stage / 'before' / (path.name + '.absent')).touch()
if not worker.is_file() or not submit.is_file():
    raise SystemExit('Existing maintenance worker and submit are required.')

expected = 'ACTIONS={"debian13-upgrade","apt-current-upgrade","reboot-host","post-upgrade-finalize"}'
expanded = expected[:-1] + ',"deploy-trud-compatible"}'
marker = '        elif req["action"]=="reboot-host":\n            unit=launch_reboot()'
dispatch = '        elif req["action"]=="deploy-trud-compatible":\n            unit=launch_exact(["/usr/local/sbin/deploy-trud-compatible"], "trud-deploy")'
for path in (worker, submit):
    text = path.read_text()
    if text.count(expected) == 1 and expanded not in text:
        text = text.replace(expected, expanded, 1)
    elif text.count(expanded) != 1:
        raise SystemExit(f'Unknown action allowlist contract: {path}')
    if path == worker:
        if dispatch not in text:
            if text.count(marker) != 1:
                raise SystemExit('Unknown worker dispatch contract.')
            text = text.replace(marker, marker + '\n' + dispatch, 1)
        if text.count(dispatch) != 1:
            raise SystemExit('Duplicate deploy dispatch.')
        tree = ast.parse(text)
        functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'launch_exact']
        if len(functions) != 1 or len(functions[0].args.args) != 2:
            raise SystemExit('Unknown launch_exact contract.')
    else:
        usage = 'usage: system-maintenance-submit debian13-upgrade|apt-current-upgrade|reboot-host|post-upgrade-finalize'
        if usage + '|deploy-trud-compatible' not in text:
            if text.count(usage) != 1:
                raise SystemExit('Unknown submit usage contract.')
            text = text.replace(usage, usage + '|deploy-trud-compatible', 1)
    compile(text, str(path), 'exec')
    (stage / 'after' / path.name).write_text(text)

# This bootstrap is pinned to the previously verified PR157 release, not HEAD.
config = (src / 'trud-release-157.conf').read_text()
expected_config = {
    'EXPECTED_LIVE_SHA': '27f62efcb8381e34f3de895c9fc41d1613c9cfd0',
    'TARGET_SHA': '7e685dfa6a331a433e916a62b1d555fe04847c4c',
    'BRANCH': 'fix/release-public-atomic-20261005',
    'DEPLOY_SCRIPT_SHA256': '7737fd784fa5c181c7e98a8a3465d665d9b4da324f67a11625c3848f56155a54',
    'RELEASE_KIND': 'security-alert-0034',
    'MIGRATION_INTENT': 'water:0034_security_alert',
    'SETUP_ROLES_INTENT': 'required',
    'SCANNER_INTENT': 'required-before-start',
}
if config != ''.join(f"{key}='{value}'\n" for key, value in expected_config.items()):
    raise SystemExit('Unexpected release manifest; review required.')
(stage / 'after' / manifest.name).write_text(config)
shutil.copy2(src / 'deploy-trud-compatible.sh', stage / 'after' / helper.name)
PY
bash -n "$STAGE/after/deploy-trud-compatible"

CHANGED=0
WATCHER_STOPPED=0
MANIFEST_PARENT_CREATED=0
INSTALL_TMP=''
atomic_install() {
    local src=$1 dest=$2 mode=$3
    INSTALL_TMP=$(mktemp "$(dirname "$dest")/.trud-bootstrap.XXXXXXXX")
    install -o root -g root -m "$mode" "$src" "$INSTALL_TMP"
    mv -f "$INSTALL_TMP" "$dest"
    INSTALL_TMP=''
}
rollback() {
    local rc=$? failed=0 path
    trap - EXIT INT TERM
    [[ "$rc" != 0 ]] || return 0
    set +e
    if [[ "$CHANGED" == 1 ]]; then
      [[ -z "$INSTALL_TMP" ]] || rm -f -- "$INSTALL_TMP" || failed=1
      systemctl stop system-maintenance.path || failed=1
      if systemctl is-active --quiet system-maintenance.service; then
        echo 'TRUD_DEPLOY_BROKER_INSTALL=RECOVERY_UNCONFIRMED (worker active)' >&2
        echo "backup=$STAGE" >&2
        exit "$rc"
      fi
      # The watcher may have been restarted after releasing this lock. Never
      # restore root assets over a newly running backup/deploy operation.
      if ! flock -n 9; then
        echo 'TRUD_DEPLOY_BROKER_INSTALL=RECOVERY_UNCONFIRMED (backup/deploy active)' >&2
        echo "backup=$STAGE" >&2
        exit "$rc"
      fi
      for path in "$WORKER" "$SUBMIT" "$HELPER" "$MANIFEST"; do
        if [[ -f "$STAGE/before/$(basename "$path")" ]]; then
            # cp preserves old file mode/owner; replacement remains atomic.
            tmp=$(mktemp "$(dirname "$path")/.trud-restore.XXXXXXXX") || { failed=1; continue; }
            cp -p "$STAGE/before/$(basename "$path")" "$tmp" && mv -f "$tmp" "$path" || failed=1
        else
            rm -f "$path" || failed=1
        fi
      done
      if [[ -f "$STAGE/before/manifest-parent.absent" && -d "$(dirname "$MANIFEST")" ]]; then
        # Remove only our newly created empty directory, never existing content.
        if [[ "$MANIFEST_PARENT_CREATED" == 1 ]]; then
            rmdir "$(dirname "$MANIFEST")" || failed=1
        else
            failed=1
        fi
      fi
    fi
    if [[ "$WATCHER_STOPPED" == 1 && "$failed" == 0 ]]; then
      systemctl start system-maintenance.path || failed=1
      systemctl is-active --quiet system-maintenance.path || failed=1
    fi
    if [[ "$failed" == 0 ]]; then
        echo 'TRUD_DEPLOY_BROKER_INSTALL=ROLLED_BACK' >&2
    else
        echo 'TRUD_DEPLOY_BROKER_INSTALL=RECOVERY_UNCONFIRMED' >&2
    fi
    echo "backup=$STAGE" >&2
    exit "$rc"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Stop only the watcher; recheck no worker started during preparation.
systemctl stop system-maintenance.path
WATCHER_STOPPED=1
if systemctl is-active --quiet system-maintenance.service; then
    echo 'maintenance request started; bootstrap aborted' >&2; exit 2
fi
if compgen -G "$INBOX/*.json" >/dev/null; then
    echo 'maintenance request queued during preparation; bootstrap aborted' >&2; exit 2
fi
if [[ -f "$STAGE/before/manifest-parent.absent" ]]; then
    [[ ! -e "$(dirname "$MANIFEST")" && ! -L "$(dirname "$MANIFEST")" ]] || {
        echo 'manifest parent changed during preparation' >&2; exit 2;
    }
fi
CHANGED=1
if [[ -f "$STAGE/before/manifest-parent.absent" ]]; then
    mkdir -m 0700 "$(dirname "$MANIFEST")"
    MANIFEST_PARENT_CREATED=1
fi
atomic_install "$STAGE/after/deploy-trud-compatible" "$HELPER" 0700
atomic_install "$STAGE/after/trud-release.conf" "$MANIFEST" 0600
atomic_install "$STAGE/after/system-maintenance-worker" "$WORKER" 0700
atomic_install "$STAGE/after/system-maintenance-submit" "$SUBMIT" 0755
# Validate before re-enabling the existing queue consumer.
cmp "$STAGE/after/deploy-trud-compatible" "$HELPER"
cmp "$STAGE/after/trud-release.conf" "$MANIFEST"
cmp "$STAGE/after/system-maintenance-worker" "$WORKER"
cmp "$STAGE/after/system-maintenance-submit" "$SUBMIT"
# A new fixed-action request may arrive once submit is installed; release the
# shared backup/deploy lock before the watcher can start its constrained worker.
flock -u 9
systemctl start system-maintenance.path
systemctl is-active --quiet system-maintenance.path
# Read-only smoke: no request is enqueued and no application service is touched.
trap - EXIT INT TERM
echo 'TRUD_DEPLOY_BROKER_INSTALL=PASS'
echo "backup=$STAGE"
echo 'next=system-maintenance-submit deploy-trud-compatible (separate release approval/preflight)'
