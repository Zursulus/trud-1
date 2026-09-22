#!/bin/bash
# Guarded release: readings review UX + XLSX audit + explicit reassignment workflow.
# Run as root: bash deploy-water-reading-admin-tools.sh TARGET_SHA EXPECTED_PRODUCTION_SHA
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /

test "$(id -u)" -eq 0
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
TARGET=${1:?Target full SHA required}
EXPECTED=${2:?Expected production full SHA required}
[[ "$TARGET" =~ ^[0-9a-f]{40}$ && "$EXPECTED" =~ ^[0-9a-f]{40}$ ]]

exec 9>/run/trud-backup.lock
flock -n 9 || { echo 'Выполняется бэкап или обновление.'; exit 1; }
gitapp() { runuser -u trudsite -- git -C "$APP" "$@"; }

test -z "$(gitapp status --porcelain)"
test "$(gitapp rev-parse HEAD)" = "$EXPECTED"
gitapp fetch origin feature/water-admin
gitapp cat-file -e "$TARGET^{commit}"
gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"
test "$(gitapp rev-parse origin/feature/water-admin)" = "$TARGET"

ALLOWED='^(backend/config/urls\.py|backend/water/reading_admin_tools\.py|backend/water/test_reading_admin_tools\.py|backend/water/templates/admin/water/reading/change_list\.html|backend/water/templates/admin/water/reading/change_form\.html|backend/water/templates/admin/water/reading/reassign\.html|backend/water/templates/admin/water/reading/review\.html|ops/deploy-water-reading-admin-tools\.sh)$'
CHANGED_FILES=$(gitapp diff --name-only "$EXPECTED" "$TARGET")
[ -n "$CHANGED_FILES" ] || { echo 'Нет изменений для установки.'; exit 1; }
while IFS= read -r path; do
    [[ "$path" =~ $ALLOWED ]] || {
        echo "Недопустимый файл в release diff: $path" >&2
        exit 1
    }
done <<< "$CHANGED_FILES"

test -f "$STATUS"
/usr/bin/python3 - "$STATUS" "$EXPECTED" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as source:
    marker = json.load(source)
if marker.get('project') != 'trud-1' or marker.get('commit') != sys.argv[2]:
    raise SystemExit('Маркер production не соответствует ожидаемой версии.')
PY
systemctl is-active --quiet trud-1-site.service

BACKUP=$(mktemp -d /var/backups/trud-water-reading-admin-tools.XXXXXXXX)
printf '%s\n' "$EXPECTED" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
cp -p "$STATUS" "$BACKUP/deployment-status.json"

manage() {
    systemd-run --quiet --wait --pipe --collect \
        --property=User=trudsite --property=Group=trudsite \
        --property=WorkingDirectory="$APP/backend" \
        --property=EnvironmentFile=/etc/trud-1-site.env \
        "$APP/.venv/bin/python" "$APP/backend/manage.py" "$@"
}
smoke() {
    local code
    code=$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/) || return 1
    test "$code" = 200
}
write_status() {
    local revision=$1 tmp
    tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX)
    printf '{"project":"trud-1","commit":"%s","deployed_at":"%s"}\n' \
        "$revision" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$tmp"
    chown root:trudsite "$tmp"
    chmod 640 "$tmp"
    mv -f "$tmp" "$STATUS"
}

STOPPED=0
CHANGED=0
rollback() {
    local result=$? failed=0
    trap - EXIT INT TERM
    [ "$result" != 0 ] || return 0
    set +e
    if [ "$CHANGED" = 1 ]; then
        gitapp checkout --detach "$EXPECTED" || failed=1
        manage collectstatic --noinput || failed=1
        cp -p "$BACKUP/deployment-status.json" "$STATUS" || failed=1
    fi
    if [ "$STOPPED" = 1 ]; then
        systemctl start trud-1-site.service || failed=1
        systemctl is-active --quiet trud-1-site.service || failed=1
        smoke || failed=1
    fi
    [ "$failed" = 0 ] && echo "Установка не завершена. Восстановлена версия $EXPECTED." \
        || echo 'Откат не подтверждён. Требуется проверка администратором.' >&2
    echo "Диагностика и копия: $BACKUP"
    exit "$result"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

STOPPED=1
systemctl stop trud-1-site.service
runuser -u postgres -- sh -c 'cd /tmp && pg_dump -Fc trud_site' > "$BACKUP/trud_site.dump"
pg_restore --list "$BACKUP/trud_site.dump" >/dev/null

CHANGED=1
gitapp checkout --detach "$TARGET"
manage check
manage migrate --check
manage collectstatic --noinput
systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service
smoke
write_status "$TARGET"

trap - EXIT INT TERM
echo "Установлена версия $TARGET. Копия: $BACKUP"
echo 'Схема БД не изменялась; улучшены сверка показаний, XLSX-аудит и подтверждаемое исправление привязки.'
