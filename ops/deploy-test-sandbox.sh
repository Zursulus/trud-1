#!/usr/bin/env bash
# Usage: sudo bash ops/deploy-test-sandbox.sh [--rollback]
# Prerequisites: /opt/trud-1-test is a clone owned by trudsite with .venv;
# trud-1-test.service runs Gunicorn on 127.0.0.1:8002 from backend/;
# Nginx listens on 127.0.0.1:8001. Both Django and the service use
# /etc/trud-1-test.env (a separate sandbox database and private-data storage).
# This script neither reads nor prints that environment file; systemd loads it.
set -Eeuo pipefail
umask 077

APP=/opt/trud-1-test
LAST_SHA="$APP/.last-deploy-sha"
TARGET_REF=origin/claude/test-release
SERVICE=trud-1-test.service

fail() {
    printf 'Ошибка: %s\n' "$*" >&2
    exit 1
}

if [[ $EUID -ne 0 || -z ${SUDO_USER:-} ]]; then
    fail 'запустите скрипт под root через sudo.'
fi
if [[ $# -gt 1 || ( $# -eq 1 && $1 != --rollback ) ]]; then
    fail 'использование: sudo bash ops/deploy-test-sandbox.sh [--rollback]'
fi

exec 9>/run/lock/trud-1-test-deploy.lock
flock -n 9 || fail 'другая выкатка тестового стенда уже выполняется.'
[[ -d "$APP/.git" ]] || fail "нет git-клона $APP."
[[ $(stat -c %U "$APP") == trudsite ]] || fail "$APP должен принадлежать trudsite."
[[ -x "$APP/.venv/bin/python" ]] || fail 'не найден Python тестового venv.'
[[ ! -L "$LAST_SHA" ]] || fail '.last-deploy-sha не должен быть символьной ссылкой.'

as_app() {
    runuser -u trudsite -- "$@"
}
git_app() {
    as_app git -C "$APP" "$@"
}
manage() {
    systemd-run --quiet --wait --pipe --collect \
        --property=User=trudsite --property=Group=trudsite \
        --property=WorkingDirectory="$APP/backend" \
        --property=EnvironmentFile=/etc/trud-1-test.env \
        "$APP/.venv/bin/python" "$APP/backend/manage.py" "$@"
}

worktree_status=$(git_app status --porcelain --untracked-files=normal -- . ':(exclude).last-deploy-sha')
[[ -z $worktree_status ]] || fail 'рабочее дерево тестового клона содержит изменения.'
cd "$APP"

trap 'printf "Выкатка не завершена. Проверьте стенд; откат кода: sudo bash ops/deploy-test-sandbox.sh --rollback.\n" >&2' ERR

if [[ ${1:-} == --rollback ]]; then
    printf 'ВНИМАНИЕ: откат только кода/зависимостей/статики; миграции и данные НЕ откатываются.\n' >&2
    printf 'Совместимость прежнего кода с текущей схемой БД должна быть проверена перед откатом.\n' >&2
    [[ -f "$LAST_SHA" ]] || fail 'не сохранён SHA для отката.'
    IFS= read -r target < "$LAST_SHA"
    [[ $target =~ ^[0-9a-f]{40}$ ]] || fail 'некорректный SHA для отката.'
    git_app cat-file -e "$target^{commit}"
    git_app checkout --detach "$target"
    mode='Откат'
else
    git_app fetch origin
    git_app rev-parse --verify "$TARGET_REF^{commit}" >/dev/null
    previous=$(git_app rev-parse HEAD)
    printf '%s\n' "$previous" | as_app tee "$LAST_SHA" >/dev/null
    git_app checkout --detach "$TARGET_REF"
    mode='Выкатка'
fi

as_app "$APP/.venv/bin/python" -m pip install -r "$APP/backend/requirements.txt"
# Deployment checks are informational; migrate/roles/static failures stop the script.
if ! manage check --deploy; then
    printf 'check --deploy сообщил о проблемах; см. вывод выше.\n' >&2
fi
if [[ ${1:-} != --rollback ]]; then
    manage migrate --noinput
    manage setup_roles
fi
# Consult Django settings without loading the env file in this shell.
manage shell -c 'from django.conf import settings; from django.core.management import call_command; call_command("collectstatic", "--noinput") if settings.STATIC_ROOT else print("STATIC_ROOT не задан: collectstatic пропущен.")'

systemctl restart "$SERVICE"
systemctl is-active --quiet "$SERVICE"
curl -fsS --connect-timeout 3 --max-time 10 --retry 10 --retry-connrefused \
    --retry-delay 1 http://127.0.0.1:8001/admin/login/ -o /dev/null
printf '%s завершена: SHA %s; %s активна; /admin/login/ доступен.\n' \
    "$mode" "$(git_app rev-parse HEAD)" "$SERVICE"
