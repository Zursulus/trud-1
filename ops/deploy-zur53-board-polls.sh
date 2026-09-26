#!/bin/bash
# Guarded production deploy for ZUR-53 board polls.
# Run as root: bash ops/deploy-zur53-board-polls.sh TARGET_SHA
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /

test "$(id -u)" -eq 0
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
EXPECTED=41fb63715bbbaf9da7062bf4aa00c1f96965bedd
TARGET=${1:?Target full SHA required}
[[ "$TARGET" =~ ^[0-9a-f]{40}$ ]]

exec 9>/run/trud-backup.lock
flock -n 9 || { echo 'Выполняется бэкап или обновление.' >&2; exit 1; }
gitapp() { runuser -u trudsite -- git -C "$APP" "$@"; }
manage() {
    systemd-run --quiet --wait --pipe --collect \
        --property=User=trudsite --property=Group=trudsite \
        --property=WorkingDirectory="$APP/backend" \
        --property=EnvironmentFile=/etc/trud-1-site.env \
        "$APP/.venv/bin/python" "$APP/backend/manage.py" "$@"
}
smoke() {
    local code attempt
    for attempt in {1..10}; do
        code=$(curl --connect-timeout 3 --max-time 8 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/ || true)
        [ "$code" = 200 ] && break
        sleep 1
    done
    test "$code" = 200
    code=$(curl --connect-timeout 3 --max-time 8 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/)
    test "$code" = 302
    code=$(curl --connect-timeout 3 --max-time 8 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/cabinet/login/)
    test "$code" = 200
    code=$(curl --connect-timeout 3 --max-time 8 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/cabinet/board/)
    test "$code" = 302
}

test -z "$(gitapp status --porcelain)"
test "$(gitapp rev-parse HEAD)" = "$EXPECTED"
test -f "$STATUS"
/usr/bin/python3 - "$STATUS" "$EXPECTED" <<'PY'
import json, sys
with open(sys.argv[1]) as source:
    marker = json.load(source)
if marker.get('project') != 'trud-1' or marker.get('commit') != sys.argv[2]:
    raise SystemExit('Маркер production не соответствует ожидаемой версии.')
PY
systemctl is-active --quiet trud-1-site.service

gitapp fetch origin feature/water-admin
test "$(gitapp rev-parse origin/feature/water-admin)" = "$TARGET"
gitapp cat-file -e "$TARGET^{commit}"
gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"

# Only the reviewed ZUR-53 implementation, its tests and this deploy helper are allowed.
while IFS= read -r path; do
    case "$path" in
        .github/workflows/backend.yml|\
        backend/config/urls.py|\
        backend/water/apps.py|\
        backend/water/board_poll_admin.py|\
        backend/water/board_poll_views.py|\
        backend/water/board_polls.py|\
        backend/water/board_portal.py|\
        backend/water/management/commands/setup_roles.py|\
        backend/water/migrations/0027_board_polls.py|\
        backend/water/static/water/board-polls.css|\
        backend/water/templates/water/portal/base.html|\
        backend/water/templates/water/portal/board_home.html|\
        backend/water/templates/water/portal/board_poll.html|\
        backend/water/templates/water/portal/more.html|\
        backend/water/test_board_polls.py|\
        ops/deploy-zur53-board-polls.sh) ;;
        *) echo "Неожиданный файл релиза: $path" >&2; exit 1 ;;
    esac
done < <(gitapp diff --name-only "$EXPECTED" "$TARGET")

test "$(gitapp diff --name-only "$EXPECTED" "$TARGET" -- backend/water/migrations | tr -d '\r')" = 'backend/water/migrations/0027_board_polls.py'
test -z "$(gitapp diff --name-only "$EXPECTED" "$TARGET" -- backend/requirements.txt backend/config/settings.py ops/backup-trud-site.sh)"

BACKUP=$(mktemp -d /var/backups/trud-zur53.XXXXXXXX)
printf '%s\n' "$EXPECTED" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
cp -p "$STATUS" "$BACKUP/deployment-status.json"
runuser -u postgres -- pg_dump -Fc trud_site > "$BACKUP/trud_site.dump"
pg_restore --list "$BACKUP/trud_site.dump" >/dev/null
if [ -d "$APP/private-data" ]; then
    tar -C "$APP" -czf "$BACKUP/private-data.tar.gz" private-data
    tar -tzf "$BACKUP/private-data.tar.gz" >/dev/null
fi

# Verify the database dump with a real isolated restore before touching production.
VERIFY_DB="trud_zur53_verify_$(date +%s)_$$"
runuser -u postgres -- createdb --template=template0 "$VERIFY_DB"
VERIFY_CREATED=1
cleanup_verify() {
    if [ "${VERIFY_CREATED:-0}" = 1 ]; then
        runuser -u postgres -- dropdb --if-exists "$VERIFY_DB" >/dev/null 2>&1 || true
    fi
}
trap cleanup_verify EXIT
runuser -u postgres -- pg_restore --exit-on-error --no-owner --no-privileges --dbname="$VERIFY_DB" < "$BACKUP/trud_site.dump"
runuser -u postgres -- psql --dbname="$VERIFY_DB" --no-psqlrc --tuples-only --no-align --command='SELECT count(*) FROM django_migrations;' >/dev/null
runuser -u postgres -- dropdb "$VERIFY_DB"
VERIFY_CREATED=0
trap - EXIT

STOPPED=0
CHANGED=0
rollback() {
    local result=$? failed=0 restore_tmp
    trap - EXIT INT TERM
    [ "$result" != 0 ] || return 0
    set +e
    if [ "$CHANGED" = 1 ]; then
        systemctl stop trud-1-site.service || failed=1
        gitapp checkout --detach "$EXPECTED" || failed=1
        manage collectstatic --noinput || failed=1
        restore_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX) || failed=1
        if [ -n "${restore_tmp:-}" ]; then
            cp -p "$BACKUP/deployment-status.json" "$restore_tmp" && mv -f "$restore_tmp" "$STATUS" || failed=1
        fi
    fi
    if [ "$STOPPED" = 1 ] && [ "$failed" = 0 ]; then
        systemctl start trud-1-site.service || failed=1
        systemctl is-active --quiet trud-1-site.service || failed=1
        smoke || failed=1
    fi
    if [ "$failed" = 0 ]; then
        echo "Установка не завершена. Код возвращён на $EXPECTED; additive migration 0027 при наличии не удалялась."
    else
        echo 'Откат кода не подтверждён. Требуется проверка администратором.' >&2
    fi
    echo "Диагностика и копия: $BACKUP"
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
manage migrate --plan | grep -q '0027_board_polls' || true
manage migrate --noinput
manage setup_roles
manage collectstatic --noinput

# Deployment creates schema/permissions only; it must not invent board members or polls.
manage shell -c "from water.board_polls import BoardMembership,BoardPoll,BoardVote; assert BoardMembership.objects.count()==0; assert BoardPoll.objects.count()==0; assert BoardVote.objects.count()==0"

systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service
smoke

status_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX)
printf '{"project":"trud-1","commit":"%s","deployed_at":"%s"}\n' "$TARGET" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$status_tmp"
chown root:trudsite "$status_tmp"
chmod 640 "$status_tmp"
mv -f "$status_tmp" "$STATUS"
status_commit=$(curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/admin/deployment-status/ | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])')
test "$status_commit" = "$TARGET"

trap - ERR EXIT INT TERM
echo "ZUR-53 установлен: $TARGET. Проверенная копия: $BACKUP"
