#!/bin/bash
# One-off production hotfix for Issue #92. Run as root:
#   bash deploy-issue-92.sh TARGET_SHA EXPECTED_SHA
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat
cd /
test "$(id -u)" -eq 0

APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
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

test -z "$(gitapp status --porcelain)"
test "$(gitapp rev-parse HEAD)" = "$EXPECTED"
gitapp cat-file -e "$TARGET^{commit}"
gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"

touched_runtime=0
while IFS= read -r path; do
    [ -n "$path" ] || continue
    case "$path" in
        backend/config/settings.py|backend/config/tests.py|backend/templates/admin/base_site.html|backend/templates/two_factor/_base.html|backend/water/test_admin_navigation.py|ops/deploy-issue-92.sh)
            ;;
        *)
            echo "Неожиданный файл в hotfix: $path" >&2
            exit 1
            ;;
    esac
    case "$path" in
        backend/config/settings.py|backend/templates/admin/base_site.html|backend/templates/two_factor/_base.html)
            touched_runtime=$((touched_runtime + 1))
            ;;
    esac
done < <(gitapp diff --name-only "$EXPECTED" "$TARGET")
test "$touched_runtime" -eq 3

test -f "$STATUS"
/usr/bin/python3 - "$STATUS" "$EXPECTED" <<'PY'
import json, sys
with open(sys.argv[1]) as source:
    marker = json.load(source)
if marker.get('project') != 'trud-1' or marker.get('commit') != sys.argv[2]:
    raise SystemExit('Маркер production не соответствует ожидаемой версии.')
PY
systemctl is-active --quiet trud-1-site.service

BACKUP=$(mktemp -d /var/backups/trud-issue-92.XXXXXXXX)
printf '%s\n' "$EXPECTED" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
cp -p "$STATUS" "$BACKUP/deployment-status.json"

smoke() {
    local attempt code page
    for attempt in {1..10}; do
        code=$(curl --connect-timeout 3 --max-time 5 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/ || true)
        [ "$code" = 302 ] && break
        sleep 1
    done
    test "$code" = 302

    page=$(mktemp)
    curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/admin/account/login/ -o "$page"
    ! grep -q 'Provide a template named' "$page"
    grep -q 'ТСН' "$page"
    rm -f "$page"

    code=$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/cabinet/login/)
    test "$code" = 200
    code=$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/)
    test "$code" = 200
}

STOPPED=0
CHANGED=0
rollback() {
    local result=$? failed=0 restore_tmp=''
    trap - EXIT INT TERM
    [ "$result" != 0 ] || return 0
    set +e
    if [ "$CHANGED" = 1 ]; then
        systemctl stop trud-1-site.service || failed=1
        gitapp checkout --detach "$EXPECTED" || failed=1
        manage collectstatic --noinput || failed=1
        restore_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX) || failed=1
        if [ -n "$restore_tmp" ]; then
            cp -p "$BACKUP/deployment-status.json" "$restore_tmp" && mv -f "$restore_tmp" "$STATUS" || failed=1
        fi
    fi
    if [ "$STOPPED" = 1 ] && [ "$failed" = 0 ]; then
        systemctl start trud-1-site.service || failed=1
        systemctl is-active --quiet trud-1-site.service || failed=1
        smoke || failed=1
    fi
    if [ "$failed" = 0 ]; then
        echo "Установка не завершена. Сохранена версия $EXPECTED."
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
CHANGED=1
gitapp checkout --detach "$TARGET"
manage check
manage migrate --check
manage collectstatic --noinput
manage shell -c "from django.conf import settings; from django.contrib.auth.password_validation import validate_password; validate_password('123456'); assert settings.AUTH_PASSWORD_VALIDATORS == [{'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 6}}]"
manage shell -c "from django.template.loader import get_template; from django.urls import reverse; assert reverse('two_factor:profile') == '/admin/account/two_factor/'; assert reverse('two_factor:disable') == '/admin/account/two_factor/disable/'; assert str(get_template('two_factor/_base.html').origin.name).endswith('/backend/templates/two_factor/_base.html'); assert str(get_template('admin/base_site.html').origin.name).endswith('/backend/templates/admin/base_site.html')"
systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service
smoke

status_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX)
printf '{"project":"trud-1","commit":"%s","deployed_at":"%s"}\n' "$TARGET" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$status_tmp"
chown root:trudsite "$status_tmp"
chmod 640 "$status_tmp"
mv -f "$status_tmp" "$STATUS"
status_commit=$(curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/admin/deployment-status/ | /usr/bin/python3 -c 'import json, sys; print(json.load(sys.stdin)["commit"])')
test "$status_commit" = "$TARGET"

trap - ERR EXIT INT TERM
echo "Установлена версия $TARGET. Копия: $BACKUP"
