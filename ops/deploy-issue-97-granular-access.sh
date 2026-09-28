#!/bin/bash
# One-off production deploy for Issue #97 granular resident access.
# Run as root: bash deploy-issue-97-granular-access.sh TARGET_SHA EXPECTED_SHA
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /
test "$(id -u)" -eq 0

APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
TARGET=${1:?Target full SHA required}
EXPECTED=${2:?Expected installed full SHA required}
[[ "$TARGET" =~ ^[0-9a-f]{40}$ && "$EXPECTED" =~ ^[0-9a-f]{40}$ ]]

exec 9>/run/trud-backup.lock
flock -n 9 || { echo 'Выполняется бэкап или обновление.'; exit 1; }
gitapp() { runuser -u trudsite -- git -C "$APP" "$@"; }
manage() {
    systemd-run --quiet --wait --pipe --collect \
        --property=User=trudsite --property=Group=trudsite \
        --property=WorkingDirectory="$APP/backend" \
        --property=EnvironmentFile=/etc/trud-1-site.env \
        "$APP/.venv/bin/python" "$APP/backend/manage.py" "$@"
}

test -z "$(gitapp status --porcelain)"
test "$(gitapp rev-parse HEAD)" = "$EXPECTED"
gitapp cat-file -e "$TARGET^{commit}"
gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"

test -f "$STATUS"
/usr/bin/python3 - "$STATUS" "$EXPECTED" <<'PY'
import json, sys
with open(sys.argv[1]) as source:
    marker = json.load(source)
if marker.get('project') != 'trud-1' or marker.get('commit') != sys.argv[2]:
    raise SystemExit('Маркер production не соответствует ожидаемой версии.')
PY
systemctl is-active --quiet trud-1-site.service
mapfile -t changed < <(gitapp diff --name-only "$EXPECTED" "$TARGET")
test "${#changed[@]}" -eq 18
for required in \
  backend/water/access_management_admin.py \
  backend/water/access_workflow.py \
  backend/water/management/commands/setup_roles.py \
  backend/water/migrations/0030_granular_resident_invite.py \
  backend/water/models.py \
  backend/water/portal.py \
  backend/water/staff_access.py \
  backend/water/staff_workspace_access_e2e_tests.py \
  backend/water/staff_workspace_urls.py \
  backend/water/templates/water/portal/invite_existing.html \
  backend/water/templates/water/portal/register.html \
  backend/water/templates/water/portal/staff_forbidden.html \
  backend/water/templates/water/work/access/dashboard.html \
  backend/water/templates/water/work/access/grant.html \
  backend/water/templates/water/work/access/invite.html \
  backend/water/templates/water/work/access/person.html \
  backend/water/test_staff_workspace_access.py \
  ops/deploy-issue-97-granular-access.sh
do
  printf '%s\n' "${changed[@]}" | grep -Fxq "$required"
done
BACKUP=$(mktemp -d /var/backups/trud-issue-97-granular-access.XXXXXXXX)
printf '%s\n' "$EXPECTED" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
cp -p "$STATUS" "$BACKUP/deployment-status.json"
runuser -u postgres -- pg_dump -Fc trud_site > "$BACKUP/trud_site.dump"
pg_restore --list "$BACKUP/trud_site.dump" >/dev/null
if [ -d "$APP/private-data" ]; then
    tar -C "$APP" -czf "$BACKUP/private-data.tar.gz" private-data
fi
echo "Копия перед обновлением: $BACKUP"

smoke() {
    local attempt code
    for attempt in {1..10}; do
        code=$(curl --connect-timeout 3 --max-time 5 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/ || true)
        [ "$code" = 302 ] && break
        sleep 1
    done
    test "$code" = 302
    test "$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/cabinet/login/)" = 200
    test "$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/)" = 200
}
STOPPED=0
CHANGED=0
rollback() {
    local result=$? failed=0 restore_tmp=''
    trap - EXIT INT TERM
    [ "$result" != 0 ] || return 0
    set +e
    echo 'Обновление остановлено; возвращаю прежний код.'
    if [ "$CHANGED" = 1 ]; then
        systemctl stop trud-1-site.service || failed=1
        gitapp checkout --detach "$EXPECTED" || failed=1
        manage collectstatic --noinput || failed=1
        restore_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX) || failed=1
        if [ -n "$restore_tmp" ]; then
            cp -p "$BACKUP/deployment-status.json" "$restore_tmp" && mv -f "$restore_tmp" "$STATUS" || failed=1
        fi
    fi
    if [ "$STOPPED" = 1 ] && [ "$failed" = 0 ]; then
        systemctl start trud-1-site.service || failed=1
        systemctl is-active --quiet trud-1-site.service || failed=1
        smoke || failed=1
    fi
    echo "Копия базы и диагностика: $BACKUP"
    exit "$result"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
STOPPED=1
systemctl stop trud-1-site.service
CHANGED=1
gitapp checkout --detach "$TARGET"
manage check
manage migrate --noinput
manage setup_roles
manage collectstatic --noinput
manage showmigrations water | grep -Fq '[X] 0030_granular_resident_invite'
systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service
smoke

status_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX)
printf '{"project":"trud-1","commit":"%s","deployed_at":"%s"}\n' "$TARGET" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$status_tmp"
chown root:trudsite "$status_tmp"
chmod 640 "$status_tmp"
mv -f "$status_tmp" "$STATUS"
status_commit=$(curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/admin/deployment-status/ | /usr/bin/python3 -c 'import json, sys; print(json.load(sys.stdin)["commit"])')
test "$status_commit" = "$TARGET"
trap - EXIT INT TERM

echo "ГОТОВО: #97 установлен. Версия: $TARGET"
echo "Главная=200, админка=302, кабинет=200. Копия: $BACKUP"
