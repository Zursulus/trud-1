#!/bin/bash
# One-time exact release for the reviewed #160 workbench contrast patch.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export SYSTEMD_PAGER=cat

[[ $(id -u) == 0 && $# == 0 ]] || { echo 'root, no arguments required' >&2; exit 2; }

EXPECTED=7e685dfa6a331a433e916a62b1d555fe04847c4c
TARGET=6fb32eff0f389eda944371d802cd14dec8eb2adb
BRANCH=fix/workbench-contrast-regression-20261006
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
DEPLOY_SHA256=ffe9d8f5cc7eff10881b94ee3e09658222bbf1ac0fa2d91aee1b859b2ab81a23
BACKUP_SHA256=790be71b5d37332e542a424e8a2ca58bb24a732fec2f7991b2010a6dce70649b
CSS_SHA256=03ef8259e570c80b91dd97034169e64e34ba8b8a0ead9224cfb3425e8dfbacfa

gitapp() { runuser -u trudsite -- git -C "$APP" "$@"; }

[[ $(gitapp rev-parse HEAD) == "$EXPECTED" ]]
[[ -z $(gitapp status --porcelain) ]]
python3 - "$STATUS" "$EXPECTED" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as source:
    marker = json.load(source)
assert marker.get('project') == 'trud-1'
assert marker.get('commit') == sys.argv[2], 'Production drift'
PY

for unit in trud-1-site.service nginx.service postgresql@17-main.service; do
    systemctl is-active --quiet "$unit"
done
nginx -t -q

gitapp fetch --no-tags origin "refs/heads/$BRANCH"
[[ $(gitapp rev-parse FETCH_HEAD) == "$TARGET" ]]
gitapp cat-file -e "$TARGET^{commit}"
gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"

EXPECTED_FILES=$'backend/water/staff_workspace_access_e2e_tests.py\nbackend/water/static/water/workbench.css\nbackend/water/templates/water/work/workbench.html'
ACTUAL_FILES=$(gitapp diff --name-only "$EXPECTED" "$TARGET" | LC_ALL=C sort)
[[ "$ACTUAL_FILES" == "$EXPECTED_FILES" ]] || {
    echo 'Unexpected #160 changed-file set:' >&2
    printf '%s\n' "$ACTUAL_FILES" >&2
    exit 2
}

# Redundant fail-closed guard: the generic compatible release must stay schema/config/dependency safe.
[[ -z $(gitapp diff --name-only "$EXPECTED" "$TARGET" --     backend/requirements.txt ':(glob)backend/**/migrations/**' backend/config/settings.py     ':(glob)backend/**/management/**' ops/backup-trud-site.sh ops/trud-1-backup.service     ops/trud-1-backup.timer) ]]

STAGE=$(mktemp -d /root/trud-release-160.XXXXXXXX)
cleanup() { rm -rf -- "$STAGE"; }
trap cleanup EXIT
gitapp show "$TARGET:ops/deploy-compatible.sh" > "$STAGE/deploy-compatible.sh"
gitapp show "$TARGET:ops/backup-trud-site.sh" > "$STAGE/backup-trud-site.sh"
chmod 700 "$STAGE/"*.sh
printf '%s  %s\n' "$DEPLOY_SHA256" "$STAGE/deploy-compatible.sh" | sha256sum -c -
printf '%s  %s\n' "$BACKUP_SHA256" "$STAGE/backup-trud-site.sh" | sha256sum -c -
bash -n "$STAGE/deploy-compatible.sh" "$STAGE/backup-trud-site.sh"

# Dedicated fresh backup with isolated restore verification before code switch.
BACKUP_ROOT=$(mktemp -d /var/backups/trud-1-release-160.XXXXXXXX)
TRUD_BACKUP_ROOT="$BACKUP_ROOT" bash "$STAGE/backup-trud-site.sh"
echo "VERIFIED_BACKUP_ROOT=$BACKUP_ROOT"

# The reviewed generic helper owns lock, its own rollback snapshot, code switch and marker update.
bash "$STAGE/deploy-compatible.sh" "$TARGET" "$EXPECTED"

for unit in trud-1-site.service nginx.service postgresql@17-main.service; do
    systemctl is-active --quiet "$unit"
done
python3 - "$STATUS" "$TARGET" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as source:
    marker = json.load(source)
assert marker.get('project') == 'trud-1'
assert marker.get('commit') == sys.argv[2]
assert marker.get('deployed_at')
PY

CSS_EXPECTED="$STAGE/workbench.css"
gitapp show "$TARGET:backend/water/static/water/workbench.css" > "$CSS_EXPECTED"
printf '%s  %s\n' "$CSS_SHA256" "$CSS_EXPECTED" | sha256sum -c -
cmp "$CSS_EXPECTED" "$APP/backend/staticfiles/water/workbench.css"
curl --fail --connect-timeout 3 --max-time 10 -sS     "https://trud-1.ru/static/water/workbench.css?release=$TARGET" | cmp - "$CSS_EXPECTED"

echo "RELEASE_160=PASS target=$TARGET"