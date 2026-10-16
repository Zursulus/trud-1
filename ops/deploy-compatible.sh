#!/bin/bash
# No arguments: root-manifest broker installed as deploy-trud-compatible.
# Two SHA arguments: historical code-only compatible installer.
if [ "$#" -eq 0 ]; then
#!/bin/bash
set -Eeuo pipefail
umask 077

MANIFEST=/etc/system-maintenance/trud-release.conf
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json

[[ $(id -u) -eq 0 ]] || { echo 'root required' >&2; exit 2; }
[[ -r "$MANIFEST" ]] || { echo 'release manifest missing' >&2; exit 2; }

# Never source a manifest writable by another user (including its parent).
for path in /etc/system-maintenance "$MANIFEST"; do
    [[ ! -L "$path" && $(stat -c '%u' "$path") -eq 0 ]]
    mode=$(stat -c '%a' "$path")
    (( (8#$mode & 0022) == 0 ))
done
[[ -f "$MANIFEST" ]]
# Root-owned manifest contains shell-quoted scalar assignments only.
# shellcheck disable=SC1090
source "$MANIFEST"
: "${EXPECTED_LIVE_SHA:?}"
: "${TARGET_SHA:?}"
: "${BRANCH:?}"
: "${DEPLOY_SCRIPT_SHA256:?}"
: "${RELEASE_KIND:?}"
: "${MIGRATION_INTENT:?}"
: "${SETUP_ROLES_INTENT:?}"
: "${SCANNER_INTENT:?}"

[[ "$EXPECTED_LIVE_SHA" =~ ^[0-9a-f]{40}$ && "$TARGET_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$DEPLOY_SCRIPT_SHA256" =~ ^[0-9a-f]{64}$ ]]
case "$RELEASE_KIND" in
    security-alert-0034) [[ "$MIGRATION_INTENT" == "water:0034_security_alert" ]] ;;
    release-20261016)
        [[ "$EXPECTED_LIVE_SHA" == "6fb32eff0f389eda944371d802cd14dec8eb2adb" ]]
        [[ "$MIGRATION_INTENT" == "account:0001_initial,account:0002_email_max_length,account:0003_alter_emailaddress_create_unique_verified_email,account:0004_alter_emailaddress_drop_unique_email,account:0005_emailaddress_idx_upper_email,account:0006_emailaddress_lower,account:0007_emailaddress_idx_email,account:0008_emailaddress_unique_primary_email_fixup,account:0009_emailaddress_unique_primary_email,forum:0001_initial,public_site:0002_publicimage_alter_publicnews_is_featured_and_more" ]]
        ;;
    *) echo 'Unsupported release kind' >&2; exit 2 ;;
esac
[[ "$SETUP_ROLES_INTENT" == "required" ]]
[[ "$SCANNER_INTENT" == "required-before-start" ]]

gitapp(){ runuser -u trudsite -- git -C "$APP" "$@"; }

live="$(python3 - "$STATUS" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding='utf-8'))['commit'])
PY
)"
[[ "$live" == "$EXPECTED_LIVE_SHA" ]]
[[ -z "$(gitapp status --porcelain)" ]]
[[ "$(gitapp rev-parse HEAD)" == "$EXPECTED_LIVE_SHA" ]]

gitapp check-ref-format --branch "$BRANCH" >/dev/null
gitapp fetch --no-tags origin "refs/heads/$BRANCH"
fetched="$(gitapp rev-parse FETCH_HEAD)"
[[ "$fetched" == "$TARGET_SHA" ]]
gitapp cat-file -e "$TARGET_SHA^{commit}"
gitapp merge-base --is-ancestor "$EXPECTED_LIVE_SHA" "$TARGET_SHA"

tmp="$(mktemp /var/backups/trud-deploy-helper.XXXXXXXX)"
trap 'rm -f "$tmp"' EXIT
gitapp show "$TARGET_SHA:ops/deploy-migration-aware.sh" >"$tmp"
printf '%s  %s\n' "$DEPLOY_SCRIPT_SHA256" "$tmp" | sha256sum -c -
chmod 700 "$tmp"

set +e
bash "$tmp" "$TARGET_SHA" "$EXPECTED_LIVE_SHA" "$RELEASE_KIND"
helper_rc=$?
set -e

local_after="$(python3 - "$STATUS" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding="utf-8"))["commit"])
PY
)"
if [[ "$helper_rc" -ne 0 ]]; then
    if [[ "$local_after" == "$TARGET_SHA" && "$(gitapp rev-parse HEAD)" == "$TARGET_SHA" ]]; then
        echo "DEPLOY_TRUD_COMPATIBLE=COMMITTED_POSTCHECK_FAILED" >&2
        echo "target=$TARGET_SHA" >&2
        exit 3
    fi
    exit "$helper_rc"
fi

postcheck_failed=0
post="$(curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/admin/deployment-status/ | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])')" || postcheck_failed=1
[[ "$post" == "$TARGET_SHA" ]] || postcheck_failed=1
for s in trud-1-site.service nginx.service postgresql@17-main.service; do systemctl is-active --quiet "$s" || postcheck_failed=1; done
[[ "$(curl -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/ || true)" == 200 ]] || postcheck_failed=1
if [[ "$postcheck_failed" -ne 0 ]]; then
    echo "DEPLOY_TRUD_COMPATIBLE=COMMITTED_POSTCHECK_FAILED" >&2
    echo "target=$TARGET_SHA" >&2
    exit 3
fi
echo "DEPLOY_TRUD_COMPATIBLE=PASS"
echo "target=$TARGET_SHA"

exit 0
fi
# Compatible releases only. Run: bash deploy-compatible.sh TARGET_SHA EXPECTED_SHA
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
test -z "$(gitapp status --porcelain)"
test "$(gitapp rev-parse HEAD)" = "$EXPECTED"
gitapp cat-file -e "$TARGET^{commit}"
gitapp merge-base --is-ancestor "$EXPECTED" "$TARGET"
# These changes require a separately reviewed migration/rollback procedure.
test -z "$(gitapp diff --name-only "$EXPECTED" "$TARGET" -- \
    backend/requirements.txt ':(glob)backend/**/migrations/**' backend/config/settings.py \
    ':(glob)backend/**/management/**' ops/backup-trud-site.sh ops/trud-1-backup.service \
    ops/trud-1-backup.timer)" || {
    echo 'Изменены схема, зависимости или настройки. Нужен отдельный план установки.'; exit 1;
}
test -f "$STATUS"
/usr/bin/python3 - "$STATUS" "$EXPECTED" <<'PY'
import json, sys
with open(sys.argv[1]) as source:
    marker = json.load(source)
if marker.get('project') != 'trud-1' or marker.get('commit') != sys.argv[2]:
    raise SystemExit('Маркер production не соответствует ожидаемой версии.')
PY
systemctl is-active --quiet trud-1-site.service
BACKUP=$(mktemp -d /var/backups/trud-compatible.XXXXXXXX)
printf '%s\n' "$EXPECTED" > "$BACKUP/previous-commit"
printf '%s\n' "$TARGET" > "$BACKUP/target-commit"
cp -p "$STATUS" "$BACKUP/deployment-status.json"
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
STOPPED=0
CHANGED=0
rollback() {
    local result=$? failed=0
    trap - EXIT INT TERM
    [ "$result" != 0 ] || return 0
    set +e
    if [ "$CHANGED" = 1 ]; then
        systemctl stop trud-1-site.service || failed=1
        gitapp checkout --detach "$EXPECTED" || failed=1
        manage collectstatic --noinput || failed=1
        # Keep the destination on the same filesystem for atomic publication.
        local restore_tmp
        restore_tmp=$(mktemp /var/lib/trud-1/deployment-status.XXXXXXXX) || failed=1
        if [ -n "$restore_tmp" ]; then
            cp -p "$BACKUP/deployment-status.json" "$restore_tmp" && \
                mv -f "$restore_tmp" "$STATUS" || failed=1
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
# EXIT also covers interruption, without leaving the service stopped.
STOPPED=1
systemctl stop trud-1-site.service
runuser -u postgres -- pg_dump -Fc trud_site > "$BACKUP/trud_site.dump"
pg_restore --list "$BACKUP/trud_site.dump" > /dev/null
if [ -d "$APP/private-data" ]; then
    tar -C "$APP" -czf "$BACKUP/private-data.tar.gz" private-data
fi
CHANGED=1
gitapp checkout --detach "$TARGET"
manage check
manage migrate --check
manage collectstatic --noinput
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
