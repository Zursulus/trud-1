#!/bin/bash
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat

APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
BRANCH=release/issue-99-production
EXPECTED=faa86c96d44404b9033c58513e5915828a30ffed
TARGET=${1:-}

[ "$(id -u)" -eq 0 ] || { echo 'run as root' >&2; exit 2; }
[[ "$TARGET" =~ ^[0-9a-f]{40}$ ]] || { echo 'usage: deploy-issue-99-access-control-v2.sh TARGET' >&2; exit 2; }

exec 9>/run/trud-backup.lock
flock -n 9 || { echo 'backup/deploy lock busy' >&2; exit 1; }

gitapp(){ runuser -u trudsite -- git -C "$APP" "$@"; }
manage(){ systemd-run --quiet --wait --pipe --collect --property=User=trudsite --property=Group=trudsite --property=WorkingDirectory="$APP/backend" --property=EnvironmentFile=/etc/trud-1-site.env "$APP/.venv/bin/python" "$APP/backend/manage.py" "$@"; }
smoke(){
  local c i
  for i in {1..10}; do c=$(curl --connect-timeout 3 --max-time 8 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/ || true); [ "$c" = 200 ] && break; sleep 1; done
  [ "$c" = 200 ]
  [ "$(curl --connect-timeout 3 --max-time 8 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/)" = 302 ]
  [ "$(curl --connect-timeout 3 --max-time 8 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/cabinet/login/)" = 200 ]
  [ "$(curl --connect-timeout 3 --max-time 8 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/work/access/)" = 302 ]
}

test -z "$(gitapp status --porcelain)"
test "$(gitapp rev-parse HEAD)" = "$EXPECTED"
/usr/bin/python3 - "$STATUS" "$EXPECTED" <<'PY'
import json,sys
m=json.load(open(sys.argv[1]))
assert m.get('project')=='trud-1' and m.get('commit')==sys.argv[2]
PY
systemctl is-active --quiet trud-1-site.service

gitapp fetch origin "$BRANCH"
test "$(gitapp rev-parse origin/$BRANCH)" = "$TARGET"
gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"

actual=$(mktemp)
expected=$(mktemp)
gitapp diff --name-only "$EXPECTED" "$TARGET" | sort >"$actual"
cat >"$expected" <<'FILES'
backend/water/access_control.py
backend/water/access_policy.py
backend/water/access_resolver.py
backend/water/access_scope.py
backend/water/access_workflow.py
backend/water/admin.py
backend/water/appeal_admin_tools.py
backend/water/appeal_workflow.py
backend/water/apps.py
backend/water/balance.py
backend/water/board_poll_views.py
backend/water/board_polls.py
backend/water/controller_workspace.py
backend/water/document_workflow.py
backend/water/finance_admin.py
backend/water/finance_workflow.py
backend/water/migrations/0032_access_control_v2_assignments.py
backend/water/models.py
backend/water/portal.py
backend/water/portal_permissions.py
backend/water/privacy_admin.py
backend/water/reading_review_config.py
backend/water/resident_models.py
backend/water/resident_numbers.py
backend/water/staff_access.py
backend/water/staff_access_v2.py
backend/water/staff_appeals.py
backend/water/staff_documents.py
backend/water/staff_finance.py
backend/water/staff_governance.py
backend/water/staff_workspace.py
backend/water/staff_workspace_access_e2e_tests.py
backend/water/staff_workspace_urls.py
backend/water/templates/admin/water/controllerreadingsubmission/change_form.html
backend/water/templates/water/portal/base.html
backend/water/templates/water/work/access/dashboard.html
backend/water/templates/water/work/access/grant.html
backend/water/templates/water/work/access/people.html
backend/water/templates/water/work/access/person_detail.html
backend/water/templates/water/work/account.html
backend/water/templates/water/work/appeal.html
backend/water/templates/water/work/base.html
backend/water/templates/water/work/dashboard.html
backend/water/templates/water/work/finance/dashboard.html
backend/water/templates/water/work/more.html
backend/water/templates/water/work/tasks.html
backend/water/templates/water/work/water.html
backend/water/test_access_control_v2.py
backend/water/test_access_policy.py
backend/water/test_access_resolver.py
backend/water/test_access_scope.py
backend/water/test_board_polls.py
backend/water/test_resident_identity_membership.py
backend/water/test_resident_numbers.py
backend/water/test_staff_access_v2.py
backend/water/test_staff_workspace_access.py
docs/ACCESS_CONTROL_INVENTORY.md
docs/ACCESS_CONTROL_V2.md
ops/deploy-issue-99-access-control-v2.sh
FILES
sort -o "$expected" "$expected"
diff -u "$expected" "$actual"
rm -f "$actual" "$expected"

test -z "$(gitapp diff --name-only "$EXPECTED" "$TARGET" -- backend/requirements.txt backend/config/settings.py ops/backup-trud-site.sh ops/trud-1-backup.service ops/trud-1-backup.timer)"
while IFS= read -r p; do
  [ -z "$p" ] && continue
  if gitapp show "$TARGET:$p" | grep -Eq 'migrations\.(RunSQL|RunPython|SeparateDatabaseAndState|RemoveField|DeleteModel|RenameField|RenameModel)'; then
    echo "unsafe migration: $p" >&2; exit 1
  fi
done < <(gitapp diff --name-only "$EXPECTED" "$TARGET" -- ':(glob)backend/**/migrations/*.py')

gitapp show "$TARGET:backend/water/migrations/0032_access_control_v2_assignments.py" | grep -Fq "('water', '0030_granular_resident_invite')"

B=$(mktemp -d /var/backups/trud-issue-99-access-v2.XXXXXXXX)
printf '%s\n' "$EXPECTED" >"$B/previous-commit"
printf '%s\n' "$TARGET" >"$B/target-commit"
cp -p "$STATUS" "$B/deployment-status.json"
runuser -u postgres -- pg_dump -Fc trud_site >"$B/trud_site.dump"
pg_restore --list "$B/trud_site.dump" >/dev/null
[ ! -d "$APP/private-data" ] || { tar -C "$APP" -czf "$B/private-data.tar.gz" private-data; tar -tzf "$B/private-data.tar.gz" >/dev/null; }

STOPPED=0
CHANGED=0
rollback(){
  rc=$?
  trap - EXIT INT TERM
  [ "$rc" = 0 ] && return 0
  set +e
  if [ "$CHANGED" = 1 ]; then
    systemctl stop trud-1-site.service
    gitapp checkout --detach "$EXPECTED"
    manage collectstatic --noinput
    t=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX)
    cp -p "$B/deployment-status.json" "$t" && mv -f "$t" "$STATUS"
  fi
  [ "$STOPPED" = 0 ] || { systemctl start trud-1-site.service; systemctl is-active --quiet trud-1-site.service; smoke; }
  echo "deploy failed; backup: $B" >&2
  exit "$rc"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

STOPPED=1
systemctl stop trud-1-site.service
CHANGED=1
gitapp checkout --detach "$TARGET"
manage check
manage migrate --plan
manage migrate --noinput
manage showmigrations water | grep -Eq '^ \[X\] 0032_access_control_v2_assignments$'
manage collectstatic --noinput
systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service
smoke

t=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX)
printf '{"project":"trud-1","commit":"%s","deployed_at":"%s"}\n' "$TARGET" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$t"
chown root:trudsite "$t"
chmod 640 "$t"
mv -f "$t" "$STATUS"
test "$(curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/admin/deployment-status/ | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])')" = "$TARGET"

trap - EXIT INT TERM
echo "deployed $TARGET; backup: $B"
