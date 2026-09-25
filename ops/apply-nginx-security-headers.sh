#!/bin/bash
set -Eeuo pipefail
umask 077

transform() {
    python3 -c "$(cat <<'PY'
import sys

source = sys.stdin.read()
marker = '    server_name trud-1.ru www.trud-1.ru;\n'
sentinel = '# ZUR-120 baseline security headers'
block = r'''
    # ZUR-120 baseline security headers. Keep this deliberately conservative:
    # these headers harden both the nginx-served public root and proxied Django
    # routes without imposing a CSP that could break the existing interface.
    add_header Strict-Transport-Security "max-age=31536000" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
'''

if sentinel in source:
    sys.stdout.write(source)
    raise SystemExit(0)
if source.count(marker) < 1:
    raise SystemExit('Не найден server_name trud-1.ru в nginx-конфигурации.')
sys.stdout.write(source.replace(marker, marker + block, 1))
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

grep -F 'ZUR-120 baseline security headers' "$CONF" >/dev/null
HEADERS=$(curl --connect-timeout 3 --max-time 10 -sSI https://trud-1.ru/)
grep -Fi 'Strict-Transport-Security: max-age=31536000' <<<"$HEADERS" >/dev/null
grep -Fi 'X-Content-Type-Options: nosniff' <<<"$HEADERS" >/dev/null
grep -Fi 'Referrer-Policy: strict-origin-when-cross-origin' <<<"$HEADERS" >/dev/null
grep -Fi 'X-Frame-Options: SAMEORIGIN' <<<"$HEADERS" >/dev/null
[ "$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/cabinet/login/)" = 200 ]

trap - EXIT INT TERM
echo "Nginx security headers установлены. Копия: $BACKUP"