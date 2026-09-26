#!/bin/bash
set -Eeuo pipefail
umask 077

transform() {
    python3 -c "$(cat <<'PY'
import sys

source = sys.stdin.read()
old_block = r'''
    # ZUR-120 baseline security headers. Keep this deliberately conservative:
    # these headers harden both the nginx-served public root and proxied Django
    # routes without imposing a CSP that could break the existing interface.
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
'''
source = source.replace(old_block, '', 1)

sentinel = '# ZUR-120 nginx-owned response headers'
block = r'''        # ZUR-120 nginx-owned response headers. Django routes keep
        # Django's own security middleware values to avoid duplicates/conflicts.
        add_header Strict-Transport-Security "max-age=31536000" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header X-Frame-Options "SAMEORIGIN" always;
'''
required = (
    '    location ^~ /admin-static/ {\n',
    '    location / {\n',
)
count = source.count(sentinel)
expected_count = len(required) + (1 if '    location @trud_payload_too_large {\n' in source else 0)
if count == expected_count:
    sys.stdout.write(source)
    raise SystemExit(0)
if count:
    raise SystemExit('Найдена частично установленная ZUR-120 конфигурация.')
for marker in required:
    if source.count(marker) != 1:
        raise SystemExit(f'Не найден ожидаемый nginx location: {marker.strip()}')
    source = source.replace(marker, marker + block, 1)
optional = '    location @trud_payload_too_large {\n'
if optional in source:
    source = source.replace(optional, optional + block, 1)
sys.stdout.write(source)
PY
)"
}

if [ "${1:-}" = "--transform" ]; then
    transform
    exit 0
fi

test "$(id -u)" -eq 0
LINK=/etc/nginx/sites-enabled/trud-1
CONF=$(readlink -f "$LINK")
test -f "$CONF"

BACKUP=$(mktemp -d /var/backups/trud-zur120-nginx.XXXXXXXX)
cp -a "$CONF" "$BACKUP/trud-1.before"
printf '%s\n' "$CONF" > "$BACKUP/config-path"
TMP=$(mktemp "${CONF}.zur120.XXXXXXXX")
CHANGED=0

rollback() {
    local rc=$?
    trap - EXIT INT TERM
    [ "$rc" -ne 0 ] || return 0
    set +e
    rm -f "$TMP"
    if [ "$CHANGED" = 1 ]; then
        cp -a "$BACKUP/trud-1.before" "$CONF"
        nginx -t && systemctl reload nginx
    fi
    echo "Изменение nginx не завершено. Копия: $BACKUP" >&2
    exit "$rc"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

transform < "$CONF" > "$TMP"
chown --reference="$CONF" "$TMP"
chmod --reference="$CONF" "$TMP"

if cmp -s "$CONF" "$TMP"; then
    rm -f "$TMP"
    nginx -t
    echo "Nginx security headers уже установлены. Копия: $BACKUP"
    trap - EXIT INT TERM
    exit 0
fi

CHANGED=1
mv -f "$TMP" "$CONF"
nginx -t
systemctl reload nginx
systemctl is-active --quiet nginx

grep -F 'ZUR-120 nginx-owned response headers' "$CONF" >/dev/null
ROOT_HEADERS=$(curl --connect-timeout 3 --max-time 10 -sSI https://trud-1.ru/)
grep -Fi 'Strict-Transport-Security: max-age=31536000' <<<"$ROOT_HEADERS" >/dev/null
grep -Fi 'X-Content-Type-Options: nosniff' <<<"$ROOT_HEADERS" >/dev/null
grep -Fi 'Referrer-Policy: strict-origin-when-cross-origin' <<<"$ROOT_HEADERS" >/dev/null
grep -Fi 'X-Frame-Options: SAMEORIGIN' <<<"$ROOT_HEADERS" >/dev/null

CABINET_HEADERS=$(curl --connect-timeout 3 --max-time 10 -sSI https://trud-1.ru/admin/cabinet/login/)
grep -F 'HTTP/1.1 200' <<<"$CABINET_HEADERS" >/dev/null
[ "$(grep -Fic 'Strict-Transport-Security:' <<<"$CABINET_HEADERS")" = 1 ]
[ "$(grep -Fic 'X-Content-Type-Options:' <<<"$CABINET_HEADERS")" = 1 ]
[ "$(grep -Fic 'Referrer-Policy:' <<<"$CABINET_HEADERS")" = 1 ]
[ "$(grep -Fic 'X-Frame-Options:' <<<"$CABINET_HEADERS")" = 1 ]
! grep -Fi 'X-Frame-Options: SAMEORIGIN' <<<"$CABINET_HEADERS" >/dev/null

trap - EXIT INT TERM
echo "Nginx security headers установлены. Копия: $BACKUP"