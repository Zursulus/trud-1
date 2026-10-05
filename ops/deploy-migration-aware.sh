#!/bin/bash
# Migration-aware release path for the reviewed SecurityAlert 0034 release.
# Run only through the checksum-pinned deploy-trud-compatible broker.
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /

test "$(id -u)" -eq 0
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
NGINX_SITE=/etc/nginx/sites-enabled/trud-1
PUBLIC_RELEASES=/var/www/trud-1/releases
TARGET=${1:?Target full SHA required}
EXPECTED=${2:?Expected installed full SHA required}
[[ "$TARGET" =~ ^[0-9a-f]{40}$ && "$EXPECTED" =~ ^[0-9a-f]{40}$ ]]

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
scanner_ready() {
    test -x /usr/bin/clamdscan
    printf 'TRUD scanner readiness probe\n' |
        runuser -u trudsite -- /usr/bin/clamdscan --stream --no-summary - >/dev/null
}

# Keep Nginx's fixed root and exchange whole directories on the same filesystem.
# Inspect inode identities on recovery: a signal after rename must not cause a
# second exchange that would accidentally publish a failed release.
exchange_public() {
    /usr/bin/python3 - "$PUBLIC_ROOT" "$PUBLIC_STAGE" "$PUBLIC_OLD_ID" "$PUBLIC_NEW_ID" "$1" <<'PY'
import ctypes, os, sys
root, stage, old, new, action = sys.argv[1:]
def identity(path):
    value = os.stat(path, follow_symlinks=False)
    return f'{value.st_dev}:{value.st_ino}'
pair = (identity(root), identity(stage))
if action == 'restore' and pair == (old, new):
    raise SystemExit(0)
expected = (old, new) if action == 'publish' else (new, old)
if pair != expected:
    raise SystemExit('Public directory identity changed; refusing exchange.')
libc = ctypes.CDLL(None, use_errno=True)
rename = libc.renameat2
rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
rename.restype = ctypes.c_int
if rename(-100, os.fsencode(root), -100, os.fsencode(stage), 2):
    number = ctypes.get_errno()
    raise OSError(number, os.strerror(number))
PY
}

verify_public() {
    local file
    for file in "${PUBLIC_FILES[@]}"; do
        cmp "$PUBLIC_ROOT/$file" "$APP/$file" || return 1
    done
}

test -z "$(gitapp status --porcelain)"
test "$(gitapp rev-parse HEAD)" = "$EXPECTED"
gitapp cat-file -e "$TARGET^{commit}"
gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"
test -f "$STATUS"
/usr/bin/python3 - "$STATUS" "$EXPECTED" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as source:
    marker = json.load(source)
if marker.get('project') != 'trud-1' or marker.get('commit') != sys.argv[2]:
    raise SystemExit('Маркер production не соответствует ожидаемой версии.')
PY
systemctl is-active --quiet trud-1-site.service
manage showmigrations water | grep -F '[X] 0033_residentaccessrequest_requester_user' >/dev/null

for path in \
    backend/requirements.txt \
    ops/backup-trud-site.sh \
    ops/trud-1-backup.service \
    ops/trud-1-backup.timer
do
    old_blob=$(gitapp rev-parse "$EXPECTED:$path")
    new_blob=$(gitapp rev-parse "$TARGET:$path")
    test "$old_blob" = "$new_blob" || {
        echo "Непредусмотренный production-файл изменён: $path" >&2
        exit 1
    }
done
EXPECTED_SENSITIVE=$(cat <<'EOF'
backend/config/settings.py
backend/water/management/commands/setup_roles.py
backend/water/management/commands/vtb_registry_dryrun.py
backend/water/migrations/0034_security_alert.py
EOF
)
ACTUAL_SENSITIVE=$(gitapp diff --name-only "$EXPECTED" "$TARGET" -- \
    backend/config/settings.py \
    ':(glob)backend/**/management/**' \
    ':(glob)backend/**/migrations/**' | sort)
test "$ACTUAL_SENSITIVE" = "$(printf '%s\n' "$EXPECTED_SENSITIVE" | sort)" || {
    echo 'Набор schema/settings/management изменений не совпадает с разрешённым intent:' >&2
    printf '%s\n' "$ACTUAL_SENSITIVE" >&2
    exit 1
}

nginx -t -q
PUBLIC_ROOT=$(/usr/bin/python3 - "$NGINX_SITE" "$PUBLIC_RELEASES" <<'PY'
import pathlib, re, sys
site, releases = map(pathlib.Path, sys.argv[1:])
roots = re.findall(r'^\s*root\s+([^;\s]+)\s*;\s*(?:#.*)?$', site.read_text(), re.M)
if len(roots) != 1:
    raise SystemExit('Expected one explicit Nginx public root.')
root = pathlib.Path(roots[0])
if root.is_symlink() or not root.is_dir() or root.resolve().parent != releases.resolve():
    raise SystemExit('Unexpected public release directory.')
print(root.resolve())
PY
)
PUBLIC_FILES=(index.html app.js style.css public-content.css
    feodosia-letter-2026-08-27.html feodosia-letter-2026-08-27.webp
    landscape.webp ordzhonikidze-sunset.webp)
PUBLIC_STAGE=$(mktemp -d "$PUBLIC_RELEASES/.trud-public.XXXXXXXX")
for file in "${PUBLIC_FILES[@]}"; do
    # Read exact Git blobs; never copy private-data or an untracked host file.
    gitapp show "$TARGET:$file" > "$PUBLIC_STAGE/$file"
    test -s "$PUBLIC_STAGE/$file"
    chmod 644 "$PUBLIC_STAGE/$file"
done
chmod 755 "$PUBLIC_STAGE"
PUBLIC_OLD_ID=$(stat -c '%d:%i' "$PUBLIC_ROOT")
PUBLIC_NEW_ID=$(stat -c '%d:%i' "$PUBLIC_STAGE")
# Rehearse exchange support before stopping the application or changing code.
probe_a=$(mktemp -d "$PUBLIC_RELEASES/.trud-exchange-a.XXXXXXXX")
probe_b=$(mktemp -d "$PUBLIC_RELEASES/.trud-exchange-b.XXXXXXXX")
(PUBLIC_ROOT=$probe_a PUBLIC_STAGE=$probe_b
 PUBLIC_OLD_ID=$(stat -c '%d:%i' "$probe_a")
 PUBLIC_NEW_ID=$(stat -c '%d:%i' "$probe_b")
 exchange_public publish
 exchange_public restore)
rmdir "$probe_a" "$probe_b"

BACKUP=$(mktemp -d /var/backups/trud-migration-0034.XXXXXXXX)
printf '%s\n' "$EXPECTED" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
cp -p "$STATUS" "$BACKUP/deployment-status.json"
tar -C "$PUBLIC_ROOT" -czf "$BACKUP/public-root.tar.gz" .
tar -tzf "$BACKUP/public-root.tar.gz" >/dev/null
printf '%s\n' "$PUBLIC_ROOT" > "$BACKUP/public-root-path"
printf '%s\n' "$PUBLIC_STAGE" > "$BACKUP/public-previous-path"

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
        # 0034 is additive. Never auto-drop its table during recovery.
        # Old code ignores the additive schema; reversing it could destroy alerts.
        manage setup_roles || failed=1
        manage collectstatic --noinput || failed=1
        exchange_public restore || failed=1
        restore_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX) || failed=1
        if [ -n "$restore_tmp" ]; then
            cp -p "$BACKUP/deployment-status.json" "$restore_tmp" &&
                mv -f "$restore_tmp" "$STATUS" || failed=1
        fi
    fi
    if [ "$STOPPED" = 1 ] && [ "$failed" = 0 ]; then
        systemctl start trud-1-site.service || failed=1
        systemctl is-active --quiet trud-1-site.service || failed=1
        smoke || failed=1
    fi
    if [ "$failed" = 0 ]; then
        echo "Установка не завершена. Production-код возвращён на $EXPECTED; аддитивная схема 0034 могла остаться применённой."
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
runuser -u postgres -- pg_dump -Fc trud_site > "$BACKUP/trud_site.dump"
pg_restore --list "$BACKUP/trud_site.dump" >/dev/null
if [ -d "$APP/private-data" ]; then
    tar -C "$APP" -czf "$BACKUP/private-data.tar.gz" private-data
fi

CHANGED_CODE=1
gitapp checkout --detach "$TARGET"
manage check
manage makemigrations --check --dry-run
manage migrate --plan
manage migrate --noinput
manage setup_roles
manage collectstatic --noinput
manage showmigrations water | grep -F '[X] 0034_security_alert' >/dev/null

# Scanner must be operational before accepting traffic from the new code.
scanner_ready
exchange_public publish
verify_public

systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service
smoke
for file in "${PUBLIC_FILES[@]}"; do
    curl --connect-timeout 3 --max-time 10 -fsS "https://trud-1.ru/$file?release=$TARGET" |
        cmp - "$APP/$file"
done

status_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX)
printf '{"project":"trud-1","commit":"%s","deployed_at":"%s"}\n' \
    "$TARGET" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$status_tmp"
chown root:trudsite "$status_tmp"
chmod 640 "$status_tmp"
mv -f "$status_tmp" "$STATUS"

# The target is now committed locally. Late observer failures must not pretend
# that rollback happened; leave the exact target installed and report status 3.
trap - EXIT INT TERM
post_rc=0
status_commit=$(curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/admin/deployment-status/ |
    /usr/bin/python3 -c 'import json, sys; print(json.load(sys.stdin)["commit"])') || post_rc=1
[ "$status_commit" = "$TARGET" ] || post_rc=1
if [ "$post_rc" -ne 0 ]; then
    echo "DEPLOY_MIGRATION_AWARE=COMMITTED_POSTCHECK_FAILED" >&2
    echo "target=$TARGET" >&2
    echo "backup=$BACKUP" >&2
    exit 3
fi

echo "DEPLOY_MIGRATION_AWARE=PASS"
echo "target=$TARGET"
echo "migration=water:0034_security_alert"
echo "public_root=$PUBLIC_ROOT"
echo "previous_public=$PUBLIC_STAGE"
echo "backup=$BACKUP"
