#!/bin/bash
# Guarded one-off deployment for ZUR-59 privacy boundary / closed member registry.
# Run as root from a script extracted from the target commit:
#   bash deploy-private-registry.sh TARGET_SHA EXPECTED_INSTALLED_SHA
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /

test "$(id -u)" -eq 0
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
TARGET=${1:?Target full SHA required}
EXPECTED=${2:?Expected installed full SHA required}
EXPECTED_RELEASE=9c65c4c38ac41c53316a89184f9cf2e7f88a96ca
[[ "$TARGET" =~ ^[0-9a-f]{40}$ && "$EXPECTED" =~ ^[0-9a-f]{40}$ ]]
test "$EXPECTED" = "$EXPECTED_RELEASE" || {
    echo "Этот deploy рассчитан только на production $EXPECTED_RELEASE." >&2
    exit 1
}

MIGRATION=backend/water/migrations/0019_member_registry_entry.py
EXPECTED_APPS_BLOB=7656a74450f38a48c4a4b85b05abfcacdd72d928
EXPECTED_PRIVATE_MODEL_BLOB=f81a2a21fd713459c104ba1a7065ef068847da40
EXPECTED_MIGRATION_BLOB=dfc7791bbfd181bcb5fdde15d045c3b34e324f17
EXPECTED_ROLES_BLOB=0558739349f7accf12b0fcf3584d4b147dc98ebb
EXPECTED_AUDIT_BLOB=9e187fb08814fa2f2629a7d1a978cabe77af6344
EXPECTED_PROVISION_BLOB=004cf770d261aaa2e9eba07115ccbb3401041dab
EXPECTED_PRIVACY_ADMIN_BLOB=e2d2c0f6032475ec7801036533ce51ff5ceee240

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

# Refuse any target containing files outside the reviewed ZUR-59 set.
ALLOWED=$(cat <<'EOF'
.github/workflows/backend.yml
backend/water/_legacy_suite.py
backend/water/apps.py
backend/water/management/commands/audit_privacy_boundary.py
backend/water/management/commands/provision_resident_test.py
backend/water/management/commands/setup_roles.py
backend/water/migrations/0019_member_registry_entry.py
backend/water/portal_ui.py
backend/water/privacy_admin.py
backend/water/private_registry.py
backend/water/templates/admin/water/index.html
backend/water/templates/water/portal/profile.html
backend/water/test_admin_navigation.py
backend/water/test_package_imports.py
backend/water/test_portal_ux.py
backend/water/test_private_registry.py
backend/water/tests.py
docs/PRIVACY.md
docs/ROLES.md
ops/deploy-private-registry.sh
EOF
)
CHANGED=$(gitapp diff --name-only "$EXPECTED" "$TARGET" | sort)
test "$CHANGED" = "$(printf '%s\n' "$ALLOWED" | sort)" || {
    echo 'Набор файлов релиза отличается от проверенного ZUR-59:' >&2
    printf '%s\n' "$CHANGED" >&2
    exit 1
}

# Critical schema/permission files must be exactly the reviewed blobs.
test "$(gitapp rev-parse "$TARGET:backend/water/apps.py")" = "$EXPECTED_APPS_BLOB"
test "$(gitapp rev-parse "$TARGET:backend/water/private_registry.py")" = "$EXPECTED_PRIVATE_MODEL_BLOB"
test "$(gitapp rev-parse "$TARGET:$MIGRATION")" = "$EXPECTED_MIGRATION_BLOB"
test "$(gitapp rev-parse "$TARGET:backend/water/management/commands/setup_roles.py")" = "$EXPECTED_ROLES_BLOB"
test "$(gitapp rev-parse "$TARGET:backend/water/management/commands/audit_privacy_boundary.py")" = "$EXPECTED_AUDIT_BLOB"
test "$(gitapp rev-parse "$TARGET:backend/water/management/commands/provision_resident_test.py")" = "$EXPECTED_PROVISION_BLOB"
test "$(gitapp rev-parse "$TARGET:backend/water/privacy_admin.py")" = "$EXPECTED_PRIVACY_ADMIN_BLOB"

BACKUP=$(mktemp -d /var/backups/trud-private-registry.XXXXXXXX)
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
        # Restore role semantics from the old release. Migration 0019 is additive
        # and may remain: the old code simply does not use its new table/permission.
        manage setup_roles || failed=1
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
        echo "Установка не завершена. Код, роли и deployment marker возвращены на $EXPECTED."
        echo 'Аддитивная миграция 0019, если успела примениться, автоматически не откатывается; старая версия её таблицу не использует.'
    else
        echo 'Откат не подтверждён. Требуется проверка администратором.' >&2
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
manage setup_roles
manage collectstatic --noinput

# Verify the permission boundary before the web service is exposed again.
manage shell -c "
from django.contrib.auth.models import Group
from water.private_registry import MemberRegistryEntry
from water.resident_numbers import ResidentNumberSlot
admin = Group.objects.get(name='Администратор ТСН')
private = Group.objects.get(name='Закрытый реестр членов ТСН')
operator = Group.objects.get(name='Оператор воды')
a = set(admin.permissions.values_list('codename', flat=True))
p = set(private.permissions.values_list('codename', flat=True))
o = set(operator.permissions.values_list('codename', flat=True))
assert 'access_private_registry' not in a
assert 'view_person' not in a and 'change_person' not in a
assert 'access_private_registry' in p and 'view_person' in p and 'change_person' in p
assert 'change_payment' not in p and 'add_reading' not in p
assert 'view_person' not in o and 'access_private_registry' not in o
assert private.user_set.count() == 0
assert not MemberRegistryEntry.objects.filter(resident_number_id=333).exists()
assert ResidentNumberSlot.objects.get(pk=333).purpose == 'test'
"
manage audit_privacy_boundary

systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service
smoke

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
echo "Установлена версия $TARGET. Закрытый реестр отделён от рабочей базы, права проверены, smoke-check пройден. Копия: $BACKUP"
