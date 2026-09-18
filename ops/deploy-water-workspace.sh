#!/bin/bash
# Run as root: bash ops/deploy-water-workspace.sh FULL_COMMIT_SHA
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
EXPECTED=5925f40318b6525e5e2a071f2111898bbed3d4ac
if [ "$PREVIOUS" != "$EXPECTED" ] && [ "$PREVIOUS" != "$TARGET" ]; then
    echo 'На сервере другая версия. Остановка для проверки совместимости.'; exit 1
fi

BACKUP=$(mktemp -d /var/backups/trud-water-workspace.XXXXXXXX)
printf '%s\n' "$PREVIOUS" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
runuser -u postgres -- pg_dump -Fc trud_site > "$BACKUP/trud_site.dump"
pg_restore --list "$BACKUP/trud_site.dump" >/dev/null
if [ -d "$APP/private-data" ]; then
    tar -C "$APP" -czf "$BACKUP/private-data.tar.gz" private-data
fi
echo "Копия перед обновлением: $BACKUP"

REQUIREMENTS=$(mktemp /tmp/trud-water-requirements.XXXXXXXX)
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
write_deployment_status() {
    revision=$1
    install -d -o root -g trudsite -m 750 /var/lib/trud-1
    status_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX)
    printf '{"project":"trud-1","commit":"%s","deployed_at":"%s"}\n' \
        "$revision" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$status_tmp"
    install -o root -g trudsite -m 640 "$status_tmp" /var/lib/trud-1/deployment-status.json
    rm -f "$status_tmp"
}
CHANGED=0
rollback() {
    result=$?
    trap - ERR
    echo 'Обновление остановлено.'
    if [ "$CHANGED" = 1 ]; then
        gitapp checkout --detach "$PREVIOUS"
        systemctl restart trud-1-site.service
        write_deployment_status "$PREVIOUS" || echo 'Не удалось восстановить маркер версии.'
        echo 'Восстановлена прежняя версия кода. Добавленные поля базы совместимы с ней.'
    fi
    echo "Копия базы: $BACKUP/trud_site.dump"
    exit "$result"
}
trap rollback ERR

CHANGED=1
systemctl stop trud-1-site.service
gitapp checkout --detach "$TARGET"
install -o root -g root -m 700 "$APP/ops/backup-trud-site.sh" /usr/local/sbin/trud-1-backup
install -o root -g root -m 644 "$APP/ops/trud-1-backup.service" /etc/systemd/system/trud-1-backup.service
install -o root -g root -m 644 "$APP/ops/trud-1-backup.timer" /etc/systemd/system/trud-1-backup.timer
bash -n /usr/local/sbin/trud-1-backup
systemd-analyze verify /etc/systemd/system/trud-1-backup.service /etc/systemd/system/trud-1-backup.timer
systemctl daemon-reload
systemctl enable --now trud-1-backup.timer
systemctl is-enabled --quiet trud-1-backup.timer
systemctl is-active --quiet trud-1-backup.timer
install -d -o trudsite -g trudsite -m 700 "$APP/private-data"
manage check
manage migrate --noinput
manage setup_roles
manage collectstatic --noinput
systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service
write_deployment_status "$TARGET"
for attempt in {1..10}; do
    code=$(curl --connect-timeout 3 --max-time 5 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/ || true)
    [ "$code" = 302 ] && break
    sleep 1
done
test "$code" = 302
code=$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/cabinet/login/)
test "$code" = 200
code=$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/)
test "$code" = 200
status_commit=$(curl --connect-timeout 3 --max-time 10 -sS https://trud-1.ru/admin/deployment-status/ \
    | /usr/bin/python3 -c 'import json, sys; print(json.load(sys.stdin)["commit"])')
test "$status_commit" = "$TARGET"
trap - ERR
echo 'ГОТОВО: публичный маркер версии установлен. Главная HTTP 200, админка HTTP 302, кабинет HTTP 200.'
echo "Версия: https://trud-1.ru/admin/deployment-status/ → $TARGET"
echo 'Расписание: ежедневно около 03:20 МСК; хранение 30 дней; каталог /var/backups/trud-1-daily.'
echo "Предыдущий коммит: $PREVIOUS; копия базы: $BACKUP/trud_site.dump"
