#!/bin/bash
# Guarded one-off deployment for ZUR-58 resident number reservations.
# Run as root from a script extracted from the target commit:
#   bash deploy-resident-numbers.sh TARGET_SHA EXPECTED_INSTALLED_SHA
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /

test "$(id -u)" -eq 0
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
TARGET=${1:?Target full SHA required}
EXPECTED=${2:?Expected installed full SHA required}
EXPECTED_RELEASE=6a8e2f24fcbbd1a531f51139c2254952213a158f
[[ "$TARGET" =~ ^[0-9a-f]{40}$ && "$EXPECTED" =~ ^[0-9a-f]{40}$ ]]
test "$EXPECTED" = "$EXPECTED_RELEASE" || {
    echo "Этот deploy рассчитан только на production $EXPECTED_RELEASE." >&2
    exit 1
}

MIGRATION=backend/water/migrations/0018_resident_number_slots.py
EXPECTED_APPS_BLOB=61cb9d0242c729324d45c10468a775dfd34dbe17
EXPECTED_MODEL_BLOB=312a1ffdb6f8bfa459eb7942ec0142cca2bb0b17
EXPECTED_MIGRATION_BLOB=3030479f5de25aa94dadcc0da171f6350a1a298c
EXPECTED_COMMAND_BLOB=a32891f619707a978a6a4e5cb9d12c7ad7cfee02

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
smoke() {
    local attempt code
    for attempt in {1..10}; do
        code=$(curl --connect-timeout 3 --max-time 5 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/ || true)
        [ "$code" = 302 ] && break
        sleep 1
    done
    test "$code" = 302 || return 1
    code=$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/cabinet/login/) || return 1
    test "$code" = 200 || return 1
    code=$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/) || return 1
    test "$code" = 200
}

# Production must still be exactly the release reviewed for this migration.
test -z "$(gitapp status --porcelain)"
CURRENT=$(gitapp rev-parse HEAD)
test "$CURRENT" = "$EXPECTED" || {
    echo "Production HEAD changed: expected $EXPECTED, got $CURRENT" >&2
    exit 1
}
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

# Refuse any target containing files outside the reviewed ZUR-58 set.
ALLOWED=$(cat <<'EOF'
.github/workflows/backend.yml
backend/water/apps.py
backend/water/management/commands/provision_resident_test.py
backend/water/migrations/0018_resident_number_slots.py
backend/water/resident_numbers.py
backend/water/test_resident_numbers.py
ops/deploy-resident-numbers.sh
EOF
)
CHANGED=$(gitapp diff --name-only "$EXPECTED" "$TARGET" | sort)
test "$CHANGED" = "$(printf '%s\n' "$ALLOWED" | sort)" || {
    echo 'Набор файлов релиза отличается от проверенного ZUR-58:' >&2
    printf '%s\n' "$CHANGED" >&2
    exit 1
}

test "$(gitapp rev-parse "$TARGET:backend/water/apps.py")" = "$EXPECTED_APPS_BLOB"
test "$(gitapp rev-parse "$TARGET:backend/water/resident_numbers.py")" = "$EXPECTED_MODEL_BLOB"
test "$(gitapp rev-parse "$TARGET:$MIGRATION")" = "$EXPECTED_MIGRATION_BLOB"
test "$(gitapp rev-parse "$TARGET:backend/water/management/commands/provision_resident_test.py")" = "$EXPECTED_COMMAND_BLOB"

BACKUP=$(mktemp -d /var/backups/trud-resident-numbers.XXXXXXXX)
printf '%s\n' "$EXPECTED" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
cp -p "$STATUS" "$BACKUP/deployment-status.json"
runuser -u postgres -- pg_dump -Fc trud_site > "$BACKUP/trud_site.dump"
pg_restore --list "$BACKUP/trud_site.dump" >/dev/null
if [ -d "$APP/private-data" ]; then
    tar -C "$APP" -czf "$BACKUP/private-data.tar.gz" private-data
fi
echo "Копия перед обновлением: $BACKUP"

STOPPED=0
CHANGED_CODE=0
rollback() {
    local result=$? failed=0 restore_tmp=''
    trap - EXIT INT TERM
    [ "$result" != 0 ] || return 0
    set +e
    if [ "$CHANGED_CODE" = 1 ]; then
        systemctl stop trud-1-site.service || failed=1
        gitapp checkout --detach "$EXPECTED" || failed=1
        manage collectstatic --noinput || failed=1
        restore_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX) || failed=1
        if [ -n "$restore_tmp" ]; then
            cp -p "$BACKUP/deployment-status.json" "$restore_tmp" && \
                mv -f "$restore_tmp" "$STATUS" || failed=1
        fi
    fi
    if [ "$STOPPED" = 1 ] && [ "$failed" = 0 ]; then
        systemctl start trud-1-site.service || failed=1
        systemctl is-active --quiet trud-1-site.service || failed=1
        smoke || failed=1
    fi
    if [ "$failed" = 0 ]; then
        echo "Установка не завершена. Код и deployment marker возвращены на $EXPECTED."
        echo 'Аддитивная миграция 0018, если успела примениться, автоматически не откатывается; старая версия её таблицу не использует.'
    else
        echo 'Откат кода не подтверждён. Требуется проверка администратором.' >&2
    fi
    echo "Диагностика и резервная копия: $BACKUP"
    exit "$result"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

STOPPED=1
systemctl stop trud-1-site.service
CHANGED_CODE=1
gitapp checkout --detach "$TARGET"
manage check
manage migrate --plan
manage migrate --noinput
manage collectstatic --noinput
systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service
smoke

# Verify the exact reservation created by the migration before publishing marker.
manage shell -c "from water.resident_numbers import ResidentNumberSlot; assert ResidentNumberSlot.objects.filter(number__gte=1, number__lte=310, purpose='resident').count() == 310; s=ResidentNumberSlot.objects.get(pk=333); assert s.purpose == 'test'"

status_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX)
printf '{"project":"trud-1","commit":"%s","deployed_at":"%s"}\n' \
    "$TARGET" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$status_tmp"
chown root:trudsite "$status_tmp"
chmod 640 "$status_tmp"
mv -f "$status_tmp" "$STATUS"
status_commit=$(curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/admin/deployment-status/ \
    | /usr/bin/python3 -c 'import json, sys; print(json.load(sys.stdin)["commit"])')
test "$status_commit" = "$TARGET"

trap - EXIT INT TERM
echo "Установлена версия $TARGET. Номера 1–310 и тестовый слот 333 зарезервированы. Smoke-check пройден. Копия: $BACKUP"
