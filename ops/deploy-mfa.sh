#!/bin/bash
# Run as root: bash ops/deploy-mfa.sh FULL_COMMIT_SHA
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /

test "$(id -u)" -eq 0
APP=/opt/trud-1-site
TARGET=${1:?Full commit SHA required}
[[ "$TARGET" =~ ^[0-9a-f]{40}$ ]]
exec 9>/run/trud-backup.lock
flock -n 9 || { echo 'Выполняется бэкап. Повторите после завершения.'; exit 1; }

gitapp() { runuser -u trudsite -- git -C "$APP" "$@"; }
test -z "$(gitapp status --porcelain --untracked-files=no)"
gitapp cat-file -e "$TARGET^{commit}"
PREVIOUS=$(gitapp rev-parse HEAD)
EXPECTED=2e2828351e18e70313de99c5d53e9e32a631ed8b
if [ "$PREVIOUS" != "$EXPECTED" ] && [ "$PREVIOUS" != "$TARGET" ]; then
    echo 'На сервере другая версия. Остановка для проверки совместимости.'; exit 1
fi

BACKUP=$(mktemp -d /var/backups/trud-mfa.XXXXXXXX)
printf '%s\n' "$PREVIOUS" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
runuser -u postgres -- pg_dump -Fc trud_site > "$BACKUP/trud_site.dump"
pg_restore --list "$BACKUP/trud_site.dump" >/dev/null
echo "Копия перед обновлением: $BACKUP"

REQUIREMENTS=$(mktemp /tmp/trud-mfa-requirements.XXXXXXXX)
trap 'rm -f "$REQUIREMENTS"' EXIT
gitapp show "$TARGET:backend/requirements.txt" > "$REQUIREMENTS"
chown trudsite:trudsite "$REQUIREMENTS"
runuser -u trudsite -- "$APP/.venv/bin/pip" install --disable-pip-version-check -r "$REQUIREMENTS"
runuser -u trudsite -- "$APP/.venv/bin/pip" check

manage() {
    systemd-run --quiet --wait --pipe --collect \
        --property=User=trudsite --property=Group=trudsite \
        --property=WorkingDirectory="$APP/backend" \
        --property=EnvironmentFile=/etc/trud-1-site.env \
        "$APP/.venv/bin/python" "$APP/backend/manage.py" "$@"
}
CHANGED=0
rollback() {
    result=$?
    trap - ERR
    echo 'Обновление остановлено.'
    if [ "$CHANGED" = 1 ]; then
        gitapp checkout --detach "$PREVIOUS"
        systemctl restart trud-1-site.service
        echo 'Восстановлена прежняя версия кода. Данные не откатывались.'
    fi
    echo "Копия базы: $BACKUP/trud_site.dump"
    exit "$result"
}
trap rollback ERR

CHANGED=1
systemctl stop trud-1-site.service
gitapp checkout --detach "$TARGET"
manage check
manage migrate --noinput
manage setup_roles
manage collectstatic --noinput
systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service
for attempt in {1..10}; do
    code=$(curl --connect-timeout 3 --max-time 5 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/ || true)
    [ "$code" = 302 ] && break
    sleep 1
done
test "$code" = 302
code=$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/)
test "$code" = 200
trap - ERR
echo 'ГОТОВО: второй фактор установлен. Главная HTTP 200, админка HTTP 302.'
echo 'Первый вход каждого сотрудника проведёт через настройку приложения-аутентификатора.'
echo "Предыдущий коммит: $PREVIOUS; копия базы: $BACKUP/trud_site.dump"
