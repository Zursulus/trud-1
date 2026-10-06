#!/bin/bash
# One-time exact production release for the verified #160 workbench contrast patch.
# Preparation of this file is not production approval.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export SYSTEMD_PAGER=cat

[[ $(id -u) -eq 0 && $# -eq 0 ]] || { echo 'root, no arguments required' >&2; exit 2; }

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
marker = json.load(open(sys.argv[1], encoding='utf-8'))
assert marker.get('project') == 'trud-1' and marker.get('commit') == sys.argv[2], 'Production drift'
PY
for unit in trud-1-site.service nginx.service postgresql@17-main.service; do
    systemctl is-active --quiet "$unit"
done
nginx -t -q
systemctl is-active --quiet system-maintenance.path
if systemctl is-active --quiet system-maintenance.service; then
    echo 'maintenance worker is active; wait for completion' >&2
    exit 2
fi
if compgen -G '/var/lib/system-maintenance/inbox/*.json' >/dev/null; then
    echo 'maintenance inbox is not empty; reconcile it before release' >&2
    exit 2
fi

gitapp fetch --no-tags origin "refs/heads/$BRANCH"
[[ $(gitapp rev-parse FETCH_HEAD) == "$TARGET" ]]
gitapp cat-file -e "$TARGET^{commit}"
gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"

EXPECTED_DIFF=$(cat <<'EOF'
backend/water/staff_workspace_access_e2e_tests.py
backend/water/static/water/workbench.css
backend/water/templates/water/work/workbench.html
EOF
)
ACTUAL_DIFF=$(gitapp diff --name-only "$EXPECTED" "$TARGET" | sort)
[[ "$ACTUAL_DIFF" == "$EXPECTED_DIFF" ]] || {
    echo 'Unexpected #160 release diff:' >&2
    printf '%s\n' "$ACTUAL_DIFF" >&2
    exit 2
}

# Fail closed if a supposedly compatible release touches any production-sensitive path.
[[ -z $(gitapp diff --name-only "$EXPECTED" "$TARGET" -- \
    backend/requirements.txt backend/config/settings.py \
    ':(glob)backend/**/migrations/**' ':(glob)backend/**/management/**' \
    ops/backup-trud-site.sh ops/trud-1-backup.service ops/trud-1-backup.timer) ]]

# Budget for restore-tested backup + deploy backup + transient restore DB; never delete backups to make room.
db_bytes=$(runuser -u postgres -- psql --no-psqlrc -At -d postgres -c "SELECT pg_database_size('trud_site');")
private_bytes=0
[[ ! -d "$APP/private-data" ]] || private_bytes=$(du -sb "$APP/private-data" | cut -f1)
free_bytes=$(df -B1 --output=avail /var/backups | tail -1 | tr -d ' ')
[[ $db_bytes =~ ^[0-9]+$ && $private_bytes =~ ^[0-9]+$ && $free_bytes =~ ^[0-9]+$ ]]
required=$((4 * db_bytes + 3 * private_bytes + 536870912))
((free_bytes >= required)) || {
    echo "SPACE_BLOCKED available=$free_bytes required=$required" >&2
    exit 2
}

STAGE=$(mktemp -d /root/trud-release-160.XXXXXXXX)
echo "RELEASE_STAGE=$STAGE"
curl --fail --location --connect-timeout 10 --max-time 60 \
    "https://raw.githubusercontent.com/Zursulus/trud-1/$TARGET/ops/deploy-compatible.sh" \
    --output "$STAGE/deploy-compatible.sh"
curl --fail --location --connect-timeout 10 --max-time 60 \
    "https://raw.githubusercontent.com/Zursulus/trud-1/$TARGET/ops/backup-trud-site.sh" \
    --output "$STAGE/backup-trud-site.sh"
curl --fail --location --connect-timeout 10 --max-time 60 \
    "https://raw.githubusercontent.com/Zursulus/trud-1/$TARGET/backend/water/static/water/workbench.css" \
    --output "$STAGE/workbench.css"
printf '%s  %s\n' "$DEPLOY_SHA256" "$STAGE/deploy-compatible.sh" | sha256sum -c -
printf '%s  %s\n' "$BACKUP_SHA256" "$STAGE/backup-trud-site.sh" | sha256sum -c -
printf '%s  %s\n' "$CSS_SHA256" "$STAGE/workbench.css" | sha256sum -c -
bash -n "$STAGE/deploy-compatible.sh" "$STAGE/backup-trud-site.sh"
grep -F '.wb a.ws-primary{color:#fff;text-decoration:none}' "$STAGE/workbench.css" >/dev/null

# Separate fresh backup root: existing backups are never overwritten or deleted.
BACKUP_ROOT=$(mktemp -d /var/backups/trud-1-release-160.XXXXXXXX)
TRUD_BACKUP_ROOT="$BACKUP_ROOT" bash "$STAGE/backup-trud-site.sh"
echo "VERIFIED_BACKUP_ROOT=$BACKUP_ROOT"

# Generic compatible deploy is already regression-tested in ops/tests/test_deploy.py.
bash "$STAGE/deploy-compatible.sh" "$TARGET" "$EXPECTED"

python3 - "$STATUS" "$TARGET" <<'PY'
import json, sys
marker = json.load(open(sys.argv[1], encoding='utf-8'))
assert marker.get('project') == 'trud-1' and marker.get('commit') == sys.argv[2], 'Installed marker mismatch'
PY
for unit in trud-1-site.service nginx.service postgresql@17-main.service; do
    systemctl is-active --quiet "$unit"
done
remote=$(curl --fail --connect-timeout 3 --max-time 10 https://trud-1.ru/admin/deployment-status/ |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])')
[[ "$remote" == "$TARGET" ]]
cmp "$STAGE/workbench.css" "$APP/backend/staticfiles/water/workbench.css"
curl --fail --connect-timeout 3 --max-time 10 \
    "https://trud-1.ru/admin-static/water/workbench.css?release=$TARGET" |
    cmp - "$STAGE/workbench.css"

echo "RELEASE_160=PASS target=$TARGET"
echo "verified_backup_root=$BACKUP_ROOT"
