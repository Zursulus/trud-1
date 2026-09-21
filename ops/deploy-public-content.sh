#!/bin/bash
# ZUR-42 release with additive public-content schema.
# Run as root: bash deploy-public-content.sh TARGET_SHA EXPECTED_SHA
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
[[ "$TARGET" =~ ^[0-9a-f]{40}$ && "$EXPECTED" =~ ^[0-9a-f]{40}$ ]]

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
    test -d "$canonical_root" || {
        echo "Public root не существует: $canonical_root" >&2
        return 1
    }
    printf '%s\n' "$canonical_root"
}
PUBLIC_ROOT=$(resolve_public_root)
echo "Публичный root Nginx: $PUBLIC_ROOT"

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
    test "$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/cabinet/login/)" = 200 || return 1
    test "$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/)" = 200 || return 1
    curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/ | grep -q 'documents-list' || return 1
    curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/admin/public/content/ \
        | /usr/bin/python3 -c 'import json,sys; x=json.load(sys.stdin); assert isinstance(x.get("news"),list) and isinstance(x.get("documents"),list)' || return 1
}

# Exact source state and release lineage are mandatory.
test -z "$(gitapp status --porcelain)"
test "$(gitapp rev-parse HEAD)" = "$EXPECTED"
gitapp cat-file -e "$TARGET^{commit}"
gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"
test -f "$STATUS"
test -f "$PUBLIC_ROOT/index.html"
/usr/bin/python3 - "$STATUS" "$EXPECTED" <<'PY'
import json, sys
with open(sys.argv[1]) as source:
    marker = json.load(source)
if marker.get('project') != 'trud-1' or marker.get('commit') != sys.argv[2]:
    raise SystemExit('Маркер production не соответствует ожидаемой версии.')
PY
systemctl is-active --quiet trud-1-site.service

# This installer is intentionally limited to ZUR-42 plus the docs-only plan commit.
unexpected=$(gitapp diff --name-only "$EXPECTED" "$TARGET" | grep -Ev '^(.github/workflows/backend.yml|app.js|index.html|public-content.css|docs/PLAN.md|backend/config/settings.py|backend/config/urls.py|backend/public_site/.*|backend/water/management/commands/setup_roles.py|backend/water/templates/admin/water/index.html|backend/water/test_admin_navigation.py|ops/deploy-public-content.sh|ops/DEPLOYMENT.md)$' || true)
if [ -n "$unexpected" ]; then
    echo 'В выпуск попали неожиданные файлы:' >&2
    printf '%s\n' "$unexpected" >&2
    exit 1
fi

test -z "$(gitapp diff --name-only "$EXPECTED" "$TARGET" -- backend/requirements.txt ops/backup-trud-site.sh ops/trud-1-backup.service ops/trud-1-backup.timer)"

BACKUP=$(mktemp -d /var/backups/trud-public-content.XXXXXXXX)
printf '%s\n' "$EXPECTED" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
cp -p "$STATUS" "$BACKUP/deployment-status.json"
mkdir "$BACKUP/public-root"
for file in index.html app.js public-content.css; do
    if [ -e "$PUBLIC_ROOT/$file" ]; then
        cp -p "$PUBLIC_ROOT/$file" "$BACKUP/public-root/$file"
    else
        : > "$BACKUP/public-root/$file.absent"
    fi
done

STOPPED=0
CHANGED=0
rollback() {
    local result=$? failed=0 restore_tmp
    trap - EXIT INT TERM
    [ "$result" != 0 ] || return 0
    set +e
    if [ "$CHANGED" = 1 ]; then
        systemctl stop trud-1-site.service || failed=1
        gitapp checkout --detach "$EXPECTED" || failed=1
        manage collectstatic --noinput || failed=1
        for file in index.html app.js public-content.css; do
            if [ -f "$BACKUP/public-root/$file" ]; then
                cp -p "$BACKUP/public-root/$file" "$PUBLIC_ROOT/$file" || failed=1
            elif [ -f "$BACKUP/public-root/$file.absent" ]; then
                rm -f "$PUBLIC_ROOT/$file" || failed=1
            fi
        done
        restore_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX) || failed=1
        if [ -n "${restore_tmp:-}" ]; then
            cp -p "$BACKUP/deployment-status.json" "$restore_tmp" && mv -f "$restore_tmp" "$STATUS" || failed=1
        fi
    fi
    if [ "$STOPPED" = 1 ] && [ "$failed" = 0 ]; then
        systemctl start trud-1-site.service || failed=1
        systemctl is-active --quiet trud-1-site.service || failed=1
    fi
    if [ "$failed" = 0 ]; then
        echo "Установка не завершена. Код и публичная страница возвращены к $EXPECTED."
        echo 'Добавленные таблицы публичного контента оставлены: старая версия их не использует.'
    else
        echo 'Откат не подтверждён. Требуется проверка администратором.' >&2
    fi
    echo "Диагностика и копия: $BACKUP"
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
    tar -tzf "$BACKUP/private-data.tar.gz" >/dev/null
fi

CHANGED=1
gitapp checkout --detach "$TARGET"
manage check
manage migrate --noinput
manage setup_roles
manage collectstatic --noinput
install -o root -g root -m 644 "$APP/index.html" "$PUBLIC_ROOT/index.html"
install -o root -g root -m 644 "$APP/app.js" "$PUBLIC_ROOT/app.js"
install -o root -g root -m 644 "$APP/public-content.css" "$PUBLIC_ROOT/public-content.css"
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

trap - ERR EXIT INT TERM
echo "Установлена версия $TARGET. Копия: $BACKUP"
echo 'Проверены: главная, публичная лента, админка и кабинет.'
