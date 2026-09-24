#!/bin/bash
# Guarded cumulative deployment from the confirmed production release to ZUR-96 target.
# Run as root from a script extracted from the exact target commit:
#   bash deploy-cumulative-zur96.sh TARGET_SHA EXPECTED_INSTALLED_SHA
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /

test "$(id -u)" -eq 0
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
TARGET=${1:?Target full SHA required}
EXPECTED=${2:?Expected installed full SHA required}
EXPECTED_RELEASE=48698fb2fd3bb2b3e4ac50473523ab763afe485f
[[ "$TARGET" =~ ^[0-9a-f]{40}$ && "$EXPECTED" =~ ^[0-9a-f]{40}$ ]]
test "$EXPECTED" = "$EXPECTED_RELEASE" || {
    echo "Этот deploy рассчитан только на production $EXPECTED_RELEASE." >&2
    exit 1
}

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

# Preflight: exact starting point, clean worktree, reachable target and matching marker.
test -z "$(gitapp status --porcelain)"
CURRENT=$(gitapp rev-parse HEAD)
test "$CURRENT" = "$EXPECTED" || {
    echo "Production HEAD changed: expected $EXPECTED, got $CURRENT" >&2
    exit 1
}
gitapp cat-file -e "$TARGET^{commit}"
gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"
gitapp merge-base --is-ancestor "$TARGET" origin/feature/water-admin

test -f "$STATUS"
/usr/bin/python3 - "$STATUS" "$EXPECTED" <<'PY'
import json, sys
with open(sys.argv[1]) as source:
    marker = json.load(source)
if marker.get('project') != 'trud-1' or marker.get('commit') != sys.argv[2]:
    raise SystemExit('Маркер production не соответствует ожидаемой версии.')
PY
systemctl is-active --quiet trud-1-site.service
manage showmigrations water | grep -F '[X] 0020_member_registry_without_fio' >/dev/null

# Production dependencies/settings/backup units must be unchanged across this cumulative release.
for path in \
    backend/requirements.txt \
    backend/config/settings.py \
    ops/backup-trud-site.sh \
    ops/trud-1-backup.service \
    ops/trud-1-backup.timer \
    ops/trud-1-site-private-data.conf
do
    old_blob=$(gitapp rev-parse "$EXPECTED:$path")
    new_blob=$(gitapp rev-parse "$TARGET:$path")
    test "$old_blob" = "$new_blob" || {
        echo "Критический production-файл изменён в накопительном релизе: $path" >&2
        exit 1
    }
done

EXPECTED_MIGRATIONS=$(cat <<'EOF'
backend/water/migrations/0021_resident_identity_tsn_membership.py
backend/water/migrations/0022_resident_access_request.py
backend/water/migrations/0023_portal_grant.py
backend/water/migrations/0024_charge_obligation.py
backend/water/migrations/0025_charge_obligation_person_plot_context.py
backend/water/migrations/0026_controller_line_access.py
EOF
)
ACTUAL_MIGRATIONS=$(gitapp diff --name-only "$EXPECTED" "$TARGET" -- backend/water/migrations | sort)
test "$ACTUAL_MIGRATIONS" = "$(printf '%s\n' "$EXPECTED_MIGRATIONS" | sort)" || {
    echo 'Набор миграций отличается от проверенного накопительного релиза:' >&2
    printf '%s\n' "$ACTUAL_MIGRATIONS" >&2
    exit 1
}

BACKUP=$(mktemp -d /var/backups/trud-cumulative-zur96.XXXXXXXX)
printf '%s\n' "$EXPECTED" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
cp -p "$STATUS" "$BACKUP/deployment-status.json"

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
        # The new schema is additive and is deliberately left in place.
        # Old production code ignores the added tables/fields; reversing schema
        # automatically would risk destroying records created during recovery.
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
        echo "Установка не завершена. Production-код возвращён на $EXPECTED; аддитивная схема могла остаться применённой."
    else
        echo 'Откат не подтверждён. Требуется проверка администратором.' >&2
    fi
    echo "Диагностика и резервная копия: $BACKUP"
    exit "$result"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Freeze application writes before making the consistent DB/private-data backup.
STOPPED=1
systemctl stop trud-1-site.service
runuser -u postgres -- pg_dump -Fc trud_site > "$BACKUP/trud_site.dump"
pg_restore --list "$BACKUP/trud_site.dump" >/dev/null
if [ -d "$APP/private-data" ]; then
    tar -C "$APP" -czf "$BACKUP/private-data.tar.gz" private-data
fi
echo "Копия перед обновлением: $BACKUP"

CHANGED_CODE=1
gitapp checkout --detach "$TARGET"
manage check
manage makemigrations --check --dry-run
manage migrate --plan
manage migrate --noinput
manage setup_roles
manage collectstatic --noinput

for migration in \
    0021_resident_identity_tsn_membership \
    0022_resident_access_request \
    0023_portal_grant \
    0024_charge_obligation \
    0025_charge_obligation_person_plot_context \
    0026_controller_line_access
do
    manage showmigrations water | grep -F "[X] $migration" >/dev/null
done

manage shell -c "
from django.apps import apps
from django.contrib.auth.models import Group
for model_name in (
    'ResidentIdentity', 'TsnMembership', 'ResidentAccessRequest',
    'PortalGrant', 'ChargeObligation', 'ControllerLineAccess',
):
    apps.get_model('water', model_name)
controller = Group.objects.get(name='Контролёр воды')
codes = set(controller.permissions.values_list('codename', flat=True))
assert 'use_controller_workspace' in codes
admin = Group.objects.get(name='Администратор ТСН')
admin_codes = set(admin.permissions.values_list('codename', flat=True))
for code in ('view_controllerlineaccess', 'add_controllerlineaccess', 'change_controllerlineaccess'):
    assert code in admin_codes
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
echo "Установлена версия $TARGET. Миграции water 0021–0026 применены, роли синхронизированы, smoke-check пройден. Реальные данные и назначения контролёров deploy не создавал. Копия: $BACKUP"
