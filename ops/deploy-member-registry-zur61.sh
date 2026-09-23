#!/bin/bash
# Guarded one-off deployment for ZUR-61: member registry without FIO.
# Run as root from a script extracted from the target commit:
#   bash deploy-member-registry-zur61.sh TARGET_SHA EXPECTED_INSTALLED_SHA
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /

test "$(id -u)" -eq 0
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
TARGET=${1:?Target full SHA required}
EXPECTED=${2:?Expected installed full SHA required}
EXPECTED_RELEASE=35a471f530716b3bcc6a04b11f21242354b94e0d
[[ "$TARGET" =~ ^[0-9a-f]{40}$ && "$EXPECTED" =~ ^[0-9a-f]{40}$ ]]
test "$EXPECTED" = "$EXPECTED_RELEASE" || {
    echo "Этот deploy рассчитан только на production $EXPECTED_RELEASE." >&2
    exit 1
}

EXPECTED_WORKFLOW_BLOB=cd265bb8cfaf878be34bd7fea263604ea9cb492f
EXPECTED_PRIVATE_MODEL_BLOB=def822095f25977aa9f27c4f57f5ed6b26ad7062
EXPECTED_MIGRATION_BLOB=95613966b81c5b61af0663975d8921b3830706f0
EXPECTED_IMPORT_BLOB=fb56519f290974f59253d4bf2fad3d438cfbba61
EXPECTED_PROVISION_BLOB=d890df009cb7f23d638df879270e3f85f2dd2612
EXPECTED_PRIVACY_ADMIN_BLOB=bb271df015a616a5d42184a21b1d2cfbf7824e90

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

ALLOWED=$(cat <<'EOF'
.github/workflows/backend.yml
backend/water/management/commands/import_member_registry.py
backend/water/management/commands/provision_resident_test.py
backend/water/migrations/0020_member_registry_without_fio.py
backend/water/privacy_admin.py
backend/water/private_registry.py
backend/water/test_import_member_registry.py
backend/water/test_private_registry.py
backend/water/test_resident_numbers.py
ops/deploy-member-registry-zur61.sh
EOF
)
CHANGED=$(gitapp diff --name-only "$EXPECTED" "$TARGET" | sort)
test "$CHANGED" = "$(printf '%s\n' "$ALLOWED" | sort)" || {
    echo 'Набор файлов релиза отличается от проверенного ZUR-61:' >&2
    printf '%s\n' "$CHANGED" >&2
    exit 1
}

test "$(gitapp rev-parse "$TARGET:.github/workflows/backend.yml")" = "$EXPECTED_WORKFLOW_BLOB"
test "$(gitapp rev-parse "$TARGET:backend/water/private_registry.py")" = "$EXPECTED_PRIVATE_MODEL_BLOB"
test "$(gitapp rev-parse "$TARGET:backend/water/migrations/0020_member_registry_without_fio.py")" = "$EXPECTED_MIGRATION_BLOB"
test "$(gitapp rev-parse "$TARGET:backend/water/management/commands/import_member_registry.py")" = "$EXPECTED_IMPORT_BLOB"
test "$(gitapp rev-parse "$TARGET:backend/water/management/commands/provision_resident_test.py")" = "$EXPECTED_PROVISION_BLOB"
test "$(gitapp rev-parse "$TARGET:backend/water/privacy_admin.py")" = "$EXPECTED_PRIVACY_ADMIN_BLOB"

BACKUP=$(mktemp -d /var/backups/trud-member-registry-zur61.XXXXXXXX)
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
MIGRATED=0
rollback() {
    local result=$? failed=0 restore_tmp=''
    trap - EXIT INT TERM
    [ "$result" != 0 ] || return 0
    set +e
    if [ "$CHANGED_CODE" = 1 ]; then
        systemctl stop trud-1-site.service || failed=1
        if [ "$MIGRATED" = 1 ]; then
            # No registry data is imported by this deploy, so 0020 is safe to reverse.
            manage migrate water 0019 --noinput || failed=1
        fi
        gitapp checkout --detach "$EXPECTED" || failed=1
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
        echo "Установка не завершена. Production возвращён на $EXPECTED."
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
manage showmigrations water | grep -F '[ ] 0020_member_registry_without_fio' >/dev/null
manage migrate --plan
manage migrate --noinput
MIGRATED=1
manage setup_roles
manage collectstatic --noinput

manage shell -c "
from django.contrib.auth.models import Group
from water.private_registry import MemberRegistryEntry
from water.resident_numbers import ResidentNumberSlot
field = MemberRegistryEntry._meta.get_field('person')
assert field.null is True
for name in ('account', 'phone', 'email', 'joined_year', 'membership_note'):
    MemberRegistryEntry._meta.get_field(name)
admin = Group.objects.get(name='Администратор ТСН')
private = Group.objects.get(name='Закрытый реестр членов ТСН')
assert 'access_private_registry' not in set(admin.permissions.values_list('codename', flat=True))
assert 'access_private_registry' in set(private.permissions.values_list('codename', flat=True))
assert not MemberRegistryEntry.objects.filter(resident_number_id=333).exists()
assert ResidentNumberSlot.objects.get(pk=333).purpose == 'test'
"

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
echo "Установлена версия $TARGET. Схема закрытого реестра без обязательного ФИО готова; данные реестра ещё НЕ импортировались. Smoke-check пройден. Копия: $BACKUP"
