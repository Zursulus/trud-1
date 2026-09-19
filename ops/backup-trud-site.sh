#!/bin/bash
# Daily, verified backup of PostgreSQL and private resident documents.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

test "$(id -u)" -eq 0
APP=/opt/trud-1-site
BACKUP_ROOT=${TRUD_BACKUP_ROOT:-/var/backups/trud-1-daily}
RETENTION_DAYS=${TRUD_BACKUP_RETENTION_DAYS:-30}
AGE_RECIPIENT=${TRUD_BACKUP_AGE_RECIPIENT:-}

case "$BACKUP_ROOT" in
    /var/backups/trud-1-*) ;;
    *) echo 'Недопустимый каталог резервных копий.' >&2; exit 2 ;;
esac
[[ "$RETENTION_DAYS" =~ ^[0-9]{1,4}$ ]] && [ "$RETENTION_DAYS" -ge 7 ] && [ "$RETENTION_DAYS" -le 3650 ]
test -d "$APP"
for command in flock pg_dump pg_restore createdb dropdb psql tar sha256sum runuser; do
    command -v "$command" >/dev/null
done
if [ -n "$AGE_RECIPIENT" ]; then
    command -v age >/dev/null || { echo 'Для шифрования установите age.' >&2; exit 2; }
fi

install -d -o root -g root -m 700 "$BACKUP_ROOT"
exec 9>/run/trud-backup.lock
flock -w 600 9 || { echo 'Не удалось дождаться освобождения блокировки резервного копирования.' >&2; exit 1; }

STAMP=$(date -u +%Y-%m-%dT%H%M%SZ)
FINAL="$BACKUP_ROOT/$STAMP"
test ! -e "$FINAL"
PARTIAL=$(mktemp -d "$BACKUP_ROOT/.partial-$STAMP.XXXXXXXX")
VERIFY_DB="trud_backup_verify_${STAMP//[^0-9]/}_$$"
VERIFY_CREATED=0
cleanup() {
    result=$?
    trap - EXIT
    if [ "$VERIFY_CREATED" = 1 ]; then
        runuser -u postgres -- dropdb --if-exists "$VERIFY_DB" >/dev/null 2>&1 || true
    fi
    if [ -n "${PARTIAL:-}" ] && [ -d "$PARTIAL" ]; then
        rm -rf -- "$PARTIAL"
    fi
    exit "$result"
}
trap cleanup EXIT

runuser -u postgres -- pg_dump -Fc trud_site > "$PARTIAL/trud_site.dump"
pg_restore --list "$PARTIAL/trud_site.dump" >/dev/null

install -d -o trudsite -g trudsite -m 700 "$APP/private-data"
tar -C "$APP" -czf "$PARTIAL/private-data.tar.gz" private-data
tar -tzf "$PARTIAL/private-data.tar.gz" >/dev/null

# A real restore into an isolated database detects damage that listing the dump
# alone cannot detect. It never connects to or changes the production database.
runuser -u postgres -- createdb --template=template0 "$VERIFY_DB"
VERIFY_CREATED=1
runuser -u postgres -- pg_restore \
    --exit-on-error --no-owner --no-privileges --dbname="$VERIFY_DB" < "$PARTIAL/trud_site.dump"
runuser -u postgres -- psql --dbname="$VERIFY_DB" --no-psqlrc --tuples-only --no-align \
    --command='SELECT count(*) FROM django_migrations;' >/dev/null
runuser -u postgres -- dropdb "$VERIFY_DB"
VERIFY_DB=
VERIFY_CREATED=0

COMMIT=$(runuser -u trudsite -- git -C "$APP" rev-parse HEAD 2>/dev/null || printf 'unknown')
{
    printf 'created_utc=%s\n' "$STAMP"
    printf 'host=%s\n' "$(hostname)"
    printf 'database=trud_site\n'
    printf 'commit=%s\n' "$COMMIT"
    printf 'private_documents=yes\n'
    printf 'restore_test=passed\n'
    if [ -n "$AGE_RECIPIENT" ]; then printf 'encryption=age\n'; else printf 'encryption=root-only\n'; fi
} > "$PARTIAL/manifest.txt"

ARTIFACTS=(trud_site.dump private-data.tar.gz manifest.txt)
if [ -n "$AGE_RECIPIENT" ]; then
    age --recipient "$AGE_RECIPIENT" --output "$PARTIAL/trud_site.dump.age" "$PARTIAL/trud_site.dump"
    age --recipient "$AGE_RECIPIENT" --output "$PARTIAL/private-data.tar.gz.age" "$PARTIAL/private-data.tar.gz"
    rm -f -- "$PARTIAL/trud_site.dump" "$PARTIAL/private-data.tar.gz"
    ARTIFACTS=(trud_site.dump.age private-data.tar.gz.age manifest.txt)
fi
(
    cd "$PARTIAL"
    sha256sum "${ARTIFACTS[@]}" > SHA256SUMS
    sha256sum --check SHA256SUMS >/dev/null
)
chmod -R go-rwx "$PARTIAL"
mv -- "$PARTIAL" "$FINAL"
PARTIAL=

# Only timestamp-shaped directories inside the validated dedicated root qualify.
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
    -name '20??-??-??T??????Z' -mtime "+$RETENTION_DAYS" -exec rm -rf -- {} +

trap - EXIT
echo "Резервная копия проверена контрольным восстановлением: $FINAL"
