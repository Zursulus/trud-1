#!/bin/bash
# Guarded follow-up deployment for ZUR-61 reserve №305 support.
# It first installs the already-reviewed base ZUR-61 release, then applies
# the importer-only follow-up that accepts optional resident slots 301-310.
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /

test "$(id -u)" -eq 0
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
TARGET=${1:?Target full SHA required}
EXPECTED=${2:?Expected installed full SHA required}
BASE_ZUR61=c2770b80e197a1e8b62f3330e25f3b29acd63767
EXPECTED_RELEASE=35a471f530716b3bcc6a04b11f21242354b94e0d
[[ "$TARGET" =~ ^[0-9a-f]{40}$ && "$EXPECTED" =~ ^[0-9a-f]{40}$ ]]
test "$EXPECTED" = "$EXPECTED_RELEASE" || {
    echo "Этот deploy рассчитан только на production $EXPECTED_RELEASE." >&2
    exit 1
}

gitapp() { runuser -u trudsite -- git -C "$APP" "$@"; }
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
    code=$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/cabinet/login/) || return 1
    test "$code" = 200
}

CURRENT=$(gitapp rev-parse HEAD)
test "$CURRENT" = "$EXPECTED" || {
    echo "Production HEAD changed: expected $EXPECTED, got $CURRENT" >&2
    exit 1
}
test -z "$(gitapp status --porcelain)"
gitapp cat-file -e "$BASE_ZUR61^{commit}"
gitapp cat-file -e "$TARGET^{commit}"
gitapp merge-base --is-ancestor "$BASE_ZUR61" "$TARGET"

# Stage 1: install the already reviewed schema release, including migration 0020.
# The base script owns /run/trud-backup.lock, so this wrapper must not hold it yet.
BASE_SCRIPT=$(mktemp /root/trud-zur61-base.XXXXXXXX)
trap 'rm -f "$BASE_SCRIPT"' EXIT
gitapp show "$BASE_ZUR61:ops/deploy-member-registry-zur61.sh" > "$BASE_SCRIPT"
chmod 700 "$BASE_SCRIPT"
bash -n "$BASE_SCRIPT"
bash "$BASE_SCRIPT" "$BASE_ZUR61" "$EXPECTED"
rm -f "$BASE_SCRIPT"
trap - EXIT

# Stage 2: importer-only follow-up. No database migration here.
exec 9>/run/trud-backup.lock
flock -n 9 || { echo 'Выполняется бэкап или обновление.'; exit 1; }

CURRENT=$(gitapp rev-parse HEAD)
test "$CURRENT" = "$BASE_ZUR61"
CHANGED=$(gitapp diff --name-only "$BASE_ZUR61" "$TARGET" | sort)
EXPECTED_CHANGED=$(printf '%s\n' \
    '.github/workflows/backend.yml' \
    'backend/water/management/commands/import_member_registry.py' \
    'backend/water/test_import_member_registry.py' \
    'ops/deploy-member-registry-reserve305.sh' | sort)
test "$CHANGED" = "$EXPECTED_CHANGED" || {
    echo 'Follow-up release содержит неожиданные файлы:' >&2
    printf '%s\n' "$CHANGED" >&2
    exit 1
}

BACKUP=$(mktemp -d /var/backups/trud-member-registry-reserve305.XXXXXXXX)
printf '%s\n' "$BASE_ZUR61" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
cp -p "$STATUS" "$BACKUP/deployment-status.json"
echo "Копия перед follow-up: $BACKUP"

rollback() {
    local rc=$?
    trap - EXIT INT TERM
    [ "$rc" = 0 ] && return 0
    set +e
    systemctl stop trud-1-site.service
    gitapp checkout --detach "$BASE_ZUR61"
    cp -p "$BACKUP/deployment-status.json" "$STATUS"
    systemctl start trud-1-site.service
    systemctl is-active --quiet trud-1-site.service
    smoke
    echo "Follow-up не установлен. Production возвращён на $BASE_ZUR61."
    echo "Диагностика: $BACKUP"
    exit "$rc"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

systemctl stop trud-1-site.service
gitapp checkout --detach "$TARGET"
manage check
manage makemigrations --check --dry-run
systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service
smoke

status_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX)
printf '{"project":"trud-1","commit":"%s","deployed_at":"%s"}\n' \
    "$TARGET" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$status_tmp"
chown root:trudsite "$status_tmp"
chmod 640 "$status_tmp"
mv -f "$status_tmp" "$STATUS"

trap - EXIT INT TERM
echo "Установлена версия $TARGET. Импортёр принимает обязательные №1–300 и при необходимости резервные №301–310. Данные реестра ещё НЕ импортировались. Smoke-check пройден. Копия: $BACKUP"
