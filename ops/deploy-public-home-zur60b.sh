#!/bin/bash
# Guarded hotfix for ZUR-60: replace the broken public scan with a readable HTML copy.
# Run as root from this script extracted from the exact target commit:
#   bash deploy-public-home-zur60b.sh TARGET_SHA EXPECTED_INSTALLED_SHA
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /

test "$(id -u)" -eq 0
APP=/opt/trud-1-site
NGINX_SITE=/etc/nginx/sites-enabled/trud-1
STATUS=/var/lib/trud-1/deployment-status.json
TARGET=${1:?Target full SHA required}
EXPECTED=${2:?Expected installed full SHA required}
EXPECTED_RELEASE=1602fb114c288c2ff3df574e8fba71174ef020e4
[[ "$TARGET" =~ ^[0-9a-f]{40}$ && "$EXPECTED" =~ ^[0-9a-f]{40}$ ]]
test "$EXPECTED" = "$EXPECTED_RELEASE" || {
    echo "Этот hotfix рассчитан только на production $EXPECTED_RELEASE." >&2
    exit 1
}

EXPECTED_INDEX_BLOB=c1e0728bd47f2bc0a08a8ee6e38f388a8943bf95
EXPECTED_LETTER_BLOB=ff20f62adb477b08a0a717211a0e7a0b8496f094

resolve_public_root() {
    local configured_root canonical_root
    test -f "$NGINX_SITE" || {
        echo "Не найден конфиг Nginx: $NGINX_SITE" >&2
        return 1
    }
    nginx -t -q
    configured_root=$(awk '$1 == "root" { sub(/;$/, "", $2); print $2; exit }' "$NGINX_SITE")
    [ -n "$configured_root" ] || {
        echo "В $NGINX_SITE не найден server root." >&2
        return 1
    }
    canonical_root=$(readlink -f "$configured_root")
    case "$canonical_root" in
        /var/www/trud-1/releases/*) ;;
        *)
            echo "Неожиданный public root Nginx: $canonical_root" >&2
            return 1
            ;;
    esac
    test -d "$canonical_root"
    printf '%s\n' "$canonical_root"
}
PUBLIC_ROOT=$(resolve_public_root)
echo "Публичный root Nginx: $PUBLIC_ROOT"

exec 9>/run/trud-backup.lock
flock -n 9 || { echo 'Выполняется бэкап или обновление.'; exit 1; }

gitapp() { runuser -u trudsite -- git -C "$APP" "$@"; }
smoke_base() {
    test "$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/)" = 200
    test "$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/)" = 302
    test "$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/cabinet/login/)" = 200
}
smoke_target() {
    local html letter headers
    smoke_base
    html=$(curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/)
    grep -q 'Оформление прав на земельные участки' <<<"$html"
    grep -q 'feodosia-letter-2026-08-27.html' <<<"$html"
    ! grep -q 'feodosia-letter-2026-08-27.webp' <<<"$html"
    ! grep -q 'Вечер в Орджоникидзе' <<<"$html"

    headers=$(curl --connect-timeout 3 --max-time 10 -fsSI https://trud-1.ru/feodosia-letter-2026-08-27.html)
    grep -qi '^content-type:.*text/html' <<<"$headers"
    letter=$(curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/feodosia-letter-2026-08-27.html)
    grep -q '№ 2-47-10604' <<<"$letter"
    grep -q 'Администрация города Феодосии' <<<"$letter"
    grep -q 'текстовая копия' <<<"$letter"
}

# Exact production baseline and release lineage.
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
backend/public_site/tests.py
feodosia-letter-2026-08-27.html
index.html
ops/deploy-public-home-zur60b.sh
EOF
)
CHANGED=$(gitapp diff --name-only "$EXPECTED" "$TARGET" | sort)
test "$CHANGED" = "$(printf '%s\n' "$ALLOWED" | sort)" || {
    echo 'Набор файлов hotfix отличается от проверенного ZUR-60b:' >&2
    printf '%s\n' "$CHANGED" >&2
    exit 1
}

test "$(gitapp rev-parse "$TARGET:index.html")" = "$EXPECTED_INDEX_BLOB"
test "$(gitapp rev-parse "$TARGET:feodosia-letter-2026-08-27.html")" = "$EXPECTED_LETTER_BLOB"

BACKUP=$(mktemp -d /var/backups/trud-public-home-zur60b.XXXXXXXX)
printf '%s\n' "$EXPECTED" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
cp -p "$STATUS" "$BACKUP/deployment-status.json"
mkdir "$BACKUP/public-root"
for file in index.html feodosia-letter-2026-08-27.html feodosia-letter-2026-08-27.webp; do
    if [ -e "$PUBLIC_ROOT/$file" ]; then
        cp -p "$PUBLIC_ROOT/$file" "$BACKUP/public-root/$file"
    else
        : > "$BACKUP/public-root/$file.absent"
    fi
done
echo "Копия перед hotfix: $BACKUP"

CHANGED_CODE=0
rollback() {
    local result=$? failed=0 restore_tmp=''
    trap - EXIT INT TERM
    [ "$result" != 0 ] || return 0
    set +e
    if [ "$CHANGED_CODE" = 1 ]; then
        gitapp checkout --detach "$EXPECTED" || failed=1
        for file in index.html feodosia-letter-2026-08-27.html feodosia-letter-2026-08-27.webp; do
            if [ -f "$BACKUP/public-root/$file" ]; then
                cp -p "$BACKUP/public-root/$file" "$PUBLIC_ROOT/$file" || failed=1
            elif [ -f "$BACKUP/public-root/$file.absent" ]; then
                rm -f "$PUBLIC_ROOT/$file" || failed=1
            fi
        done
        restore_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX) || failed=1
        if [ -n "$restore_tmp" ]; then
            cp -p "$BACKUP/deployment-status.json" "$restore_tmp" && mv -f "$restore_tmp" "$STATUS" || failed=1
        fi
    fi
    if [ "$failed" = 0 ]; then
        smoke_base || failed=1
    fi
    if [ "$failed" = 0 ]; then
        echo "Hotfix не завершён. Код, публичные файлы и deployment marker возвращены на $EXPECTED."
    else
        echo 'Откат не подтверждён. Требуется проверка администратором.' >&2
    fi
    echo "Диагностика и резервная копия: $BACKUP"
    exit "$result"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

CHANGED_CODE=1
gitapp checkout --detach "$TARGET"

for file in index.html feodosia-letter-2026-08-27.html; do
    tmp=$(mktemp "$PUBLIC_ROOT/.zur60b-${file//\//_}.XXXXXXXX")
    install -o root -g root -m 644 "$APP/$file" "$tmp"
    mv -f "$tmp" "$PUBLIC_ROOT/$file"
done
# The previously published WEBP was corrupt and is deliberately retired.
rm -f "$PUBLIC_ROOT/feodosia-letter-2026-08-27.webp"

smoke_target

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
echo "Установлена версия $TARGET. Письмо теперь открывается как читаемая HTML-копия; сломанный WEBP удалён из public root. Smoke-check пройден. Копия: $BACKUP"
