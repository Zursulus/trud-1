#!/bin/bash
# Additive ZUR-136 release: performance tests + read-only integrity audit command.
# Run as root: bash ops/deploy-zur136-business-audit.sh TARGET_SHA
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /

test "$(id -u)" -eq 0
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
EXPECTED=ffb1808aa55f0bd4b749b0b83deed22a3cbc3d97
TARGET=${1:?Target full SHA required}
[[ "$TARGET" =~ ^[0-9a-f]{40}$ ]]

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

ALLOWED='^(backend/water/test_performance_baseline.py|backend/water/management/commands/audit_business_integrity.py|backend/water/test_business_integrity_audit.py|ops/deploy-zur136-business-audit.sh|ops/tests/test_zur136_deploy.py)$'
UNEXPECTED=$(gitapp diff --name-only "$EXPECTED" "$TARGET" | grep -Ev "$ALLOWED" || true)
test -z "$UNEXPECTED" || { echo 'Найдены неожиданные файлы в релизе:'; echo "$UNEXPECTED"; exit 1; }

test -f "$STATUS"
/usr/bin/python3 - "$STATUS" "$EXPECTED" <<'PY'
import json, sys
with open(sys.argv[1]) as source:
    marker = json.load(source)
if marker.get('project') != 'trud-1' or marker.get('commit') != sys.argv[2]:
    raise SystemExit('Маркер production не соответствует ожидаемой версии.')
PY
systemctl is-active --quiet trud-1-site.service

BACKUP=$(mktemp -d /var/backups/trud-zur136.XXXXXXXX)
printf '%s\n' "$EXPECTED" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
cp -p "$STATUS" "$BACKUP/deployment-status.json"
runuser -u postgres -- pg_dump -Fc trud_site > "$BACKUP/trud_site.dump"
pg_restore --list "$BACKUP/trud_site.dump" >/dev/null
if [ -d "$APP/private-data" ]; then
    tar -C "$APP" -czf "$BACKUP/private-data.tar.gz" private-data
    tar -tzf "$BACKUP/private-data.tar.gz" >/dev/null
fi
echo "Копия перед ZUR-136: $BACKUP"

CHANGED=0
STOPPED=0
rollback() {
    rc=$?
    trap - EXIT INT TERM
    [ "$rc" -ne 0 ] || return 0
    set +e
    if [ "$CHANGED" = 1 ]; then
        systemctl stop trud-1-site.service
        gitapp checkout --detach "$EXPECTED"
        cp -p "$BACKUP/deployment-status.json" "$STATUS"
    fi
    if [ "$STOPPED" = 1 ]; then
        systemctl start trud-1-site.service
    fi
    echo "Установка остановлена; восстановлен $EXPECTED. Диагностика: $BACKUP" >&2
    exit "$rc"
}
trap rollback EXIT INT TERM

STOPPED=1
systemctl stop trud-1-site.service
CHANGED=1
gitapp checkout --detach "$TARGET"
manage check
manage migrate --check
systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service

for url in https://trud-1.ru/ https://trud-1.ru/admin/cabinet/login/; do
    code=$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' "$url")
    test "$code" = 200
 done
code=$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/)
test "$code" = 302

status_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX)
printf '{"project":"trud-1","commit":"%s","deployed_at":"%s"}\n' "$TARGET" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$status_tmp"
chown root:trudsite "$status_tmp"
chmod 640 "$status_tmp"
mv -f "$status_tmp" "$STATUS"

trap - EXIT INT TERM
echo "ZUR-136 установлен: $TARGET; backup: $BACKUP"
