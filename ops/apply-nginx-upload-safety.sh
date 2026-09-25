#!/bin/bash
set -Eeuo pipefail
umask 077

transform() {
    python3 -c "$(cat <<'PY'
import sys

source = sys.stdin.read()
marker = '    server_name trud-1.ru www.trud-1.ru;\n'
sentinel = '# ZUR-112 portal upload safety'
block = r'''
    # ZUR-112 portal upload safety: Django accepts appeal attachments up to
    # 10 MiB. Keep nginx slightly above that so application validation can
    # return the normal form error instead of nginx's generic 413 page.
    client_max_body_size 12m;
    error_page 413 = @trud_payload_too_large;

    location @trud_payload_too_large {
        default_type text/html;
        charset utf-8;
        add_header Cache-Control "no-store" always;
        return 413 '<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Файл слишком большой</title></head><body style="font-family:system-ui,sans-serif;max-width:42rem;margin:10vh auto;padding:24px"><h1>Файл слишком большой</h1><p>Максимальный размер вложения — 10 МБ.</p><p>Вернитесь назад и выберите файл меньшего размера.</p></body></html>';
    }
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

BACKUP=$(mktemp -d /var/backups/trud-zur112-nginx.XXXXXXXX)
cp -a "$CONF" "$BACKUP/trud-1.before"
printf '%s\n' "$CONF" > "$BACKUP/config-path"
TMP=$(mktemp "${CONF}.zur112.XXXXXXXX")
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
    echo "Nginx upload safety уже установлена. Копия: $BACKUP"
    trap - EXIT INT TERM
    exit 0
fi

CHANGED=1
mv -f "$TMP" "$CONF"
nginx -t
systemctl reload nginx
systemctl is-active --quiet nginx
nginx -T 2>/dev/null | grep -q 'client_max_body_size 12m;'
nginx -T 2>/dev/null | grep -q 'ZUR-112 portal upload safety'
[ "$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/)" = 200 ]
[ "$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/cabinet/login/)" = 200 ]

trap - EXIT INT TERM
echo "Nginx upload safety установлена. Копия: $BACKUP"