#!/bin/bash
# Релиз навигации 19.10.2026; процедура docs/RELEASE-20261017.md. Запускать root.
# --check пишет только временные файлы и transient systemd journal, не production.
# Ни migrate --noinput, ни установка пакетов, ни восстановление БД не выполняются.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C.UTF-8
export GIT_OPTIONAL_LOCKS=0
STEP=0 BACKUP='' WORK='' PUBLIC_STAGE='' PROBE='' CHANGING=0
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
PUBLIC_ROOT=/var/www/trud-1/releases/bef1cb7
MODE=${1:-} TARGET_SHA=${2:-}
step() { STEP=$((STEP+1)); printf 'ШАГ %s: %s\n' "$STEP" "$1"; }
ok() { printf 'ШАГ %s: OK\n' "$STEP"; }
cleanup() {
    local rc=$?
    trap - EXIT
    if [[ -n $WORK && $WORK == /opt/trud-release-check-20261019.* ]]; then rm -rf -- "$WORK"; fi
    if [[ -n $PROBE && $PROBE == /var/www/trud-1/releases/.trud-exchange-probe.* ]]; then rm -rf -- "$PROBE"; fi
    # После начала изменений соседний каталог нужен даже при сбое отката.
    if [[ $CHANGING == 0 && -n $PUBLIC_STAGE ]]; then rm -rf -- "$PUBLIC_STAGE"; fi
    exit "$rc"
}
failed() {
    local rc=$?
    trap - ERR INT TERM
    [[ $rc != 0 ]] || rc=1
    printf 'ШАГ %s: ОШИБКА\n' "$STEP" >&2
    if [[ $CHANGING == 1 ]]; then
        printf 'Автоматический откат; BACKUP=%s\n' "$BACKUP" >&2
        # Запускать отдельной командой: errexit внутри rollback остаётся активным.
        set +e
        bash "$BACKUP/rollback.sh" "$BACKUP"
        local rollback_rc=$?
        set -e
        if [[ $rollback_rc != 0 ]]; then
            printf 'ОШИБКА: откат не подтверждён; требуется ручное восстановление.\n' >&2
        fi
    fi
    printf 'ИТОГ: релиз не выполнен. BACKUP=%s\n' "${BACKUP:-не создан}" >&2
    exit "$rc"
}
trap cleanup EXIT
trap failed ERR INT TERM
if [[ $# != 2 || ( $MODE != --check && $MODE != --apply ) || ! $TARGET_SHA =~ ^[0-9a-f]{40}$ ]]; then
    echo 'Использование: bash release-redesign-20261019.sh --check|--apply <TARGET_SHA:40 hex>' >&2
    exit 2
fi
[[ $(id -u) == 0 ]]
cd /
gitapp() { runuser -u trudsite -- git -C "$APP" "$@"; }
gitstage() { runuser -u trudsite -- git --git-dir="$WORK/repository.git" "$@"; }
manage_at() {
    local directory=$1; shift
    systemd-run --quiet --wait --pipe --collect \
        --property=User=trudsite --property=Group=trudsite \
        --property="WorkingDirectory=$directory/backend" \
        --property=EnvironmentFile=/etc/trud-1-site.env \
        --setenv=PYTHONDONTWRITEBYTECODE=1 --setenv=PGOPTIONS=-cdefault_transaction_read_only=on \
        "$APP/.venv/bin/python" "$directory/backend/manage.py" "$@"
}
baseline() {
    python3 - "$STATUS" <<'PY'
import json,subprocess,sys
p=json.load(open(sys.argv[1]))
r=json.loads(subprocess.check_output(['curl','--connect-timeout','3','--max-time','10','-fsS','https://trud-1.ru/admin/deployment-status/']))
h=subprocess.check_output(['runuser','-u','trudsite','--','git','-C','/opt/trud-1-site','rev-parse','HEAD'],text=True).strip()
assert p['project']==r['project']=='trud-1'
assert p['commit']==r['commit']==h and h=='ef8e510a825e4a4af641d6fb700d48417f8d05f7', 'Baseline отличается от ef8e510a825e4a4af641d6fb700d48417f8d05f7'
print(h)
PY
}
empty_plan() {
    local directory=$1 plan
    plan=$(manage_at "$directory" migrate --plan)
    # Проверяется полный вывод, а не наличие строки среди непустых операций.
    [[ $plan == 'Planned operations:'$'\n''  No planned migration operations.' ]]
    manage_at "$directory" migrate --check
}
exchange() {
    python3 - "$1" "$2" <<'PY'
import ctypes,os,sys
f=ctypes.CDLL(None,use_errno=True).renameat2
f.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
f.restype=ctypes.c_int
if f(-100,os.fsencode(sys.argv[1]),-100,os.fsencode(sys.argv[2]),2):
 n=ctypes.get_errno(); raise OSError(n,os.strerror(n))
PY
}
step 'Конфигурация сервера, сервисы и Nginx'
for command in python3 runuser git tar curl flock systemctl systemd-run journalctl nginx pg_dump pg_restore psql sha256sum; do command -v "$command" >/dev/null; done
[[ $(systemctl show trud-1-site.service -p User --value) == trudsite ]]
[[ $(systemctl show trud-1-site.service -p Group --value) == trudsite ]]
[[ $(systemctl show trud-1-site.service -p WorkingDirectory --value) == "$APP/backend" ]]
[[ $(systemctl show trud-1-site.service -p EnvironmentFiles --value) == '/etc/trud-1-site.env (ignore_errors=no)' ]]
[[ $(systemctl show trud-1-site.service -p ExecStart --value) == *'/opt/trud-1-site/.venv/'* ]]
[[ $(systemctl show trud-1-backup.service -p ExecStart --value) == *'path=/usr/local/sbin/trud-1-backup ;'* ]]
for unit in trud-1-site.service nginx.service postgresql.service; do systemctl is-active --quiet "$unit"; done
nginx -t >/dev/null 2>&1
python3 - <<'PY'
import pathlib,re
p=pathlib.Path('/etc/nginx/sites-enabled/trud-1')
r=re.findall(r'^\s*root\s+([^;\s]+)\s*;\s*(?:#.*)?$',p.read_text(),re.M)
assert r==['/var/www/trud-1/releases/bef1cb7'], 'Неожиданный Nginx root'
a=pathlib.Path(r[0]); assert not a.is_symlink() and a.is_dir() and a.resolve()==a
PY
ok
step 'Baseline marker + HEAD, чистота дерева и источник'
[[ -f $STATUS && ! -L $STATUS && $(stat -c %u "$STATUS") == 0 ]]
EXPECTED_SHA=ef8e510a825e4a4af641d6fb700d48417f8d05f7
[[ $(baseline) == "$EXPECTED_SHA" ]]
[[ -z $(gitapp status --porcelain) ]]
[[ $(gitapp remote get-url origin) == git@github.com:Zursulus/trud-1.git ]]
runuser -u trudsite -- "$APP/.venv/bin/python" -c 'from importlib.metadata import version; assert version("django-allauth")=="65.18.0"'
ok
step 'Память и консервативный резерв места на каждом filesystem'
DB_BYTES=$(runuser -u postgres -- psql -XAt -d trud_site -c 'SELECT pg_database_size(current_database());')
[[ $DB_BYTES =~ ^[0-9]+$ ]]
TREE_BYTES=$(du -sx -B1 "$APP" "$PUBLIC_ROOT" | awk '{s+=$1} END {printf "%.0f",s}')
NEEDED=$((5*DB_BYTES + 3*TREE_BYTES + 512*1024*1024))
for path in /opt /var/backups /var/www/trud-1/releases /var/lib/trud-1; do
    FREE=$(df -B1 --output=avail "$path" | tail -n 1)
    (( FREE >= NEEDED ))
done
AVAILABLE=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
(( AVAILABLE >= 256*1024 ))
printf 'Требуется резерв: %s байт; MemAvailable: %s КиБ\n' "$NEEDED" "$AVAILABLE"
ok
if [[ $MODE == --apply ]]; then
    # Отдельный lock исключает параллельные выпуски, не блокируя штатный backup.
    exec 8>/run/trud-release-20261019.lock
    flock -n 8
fi
step 'Изолированный fetch ветки, точный SHA, ancestry и запрет несовместимого diff'
WORK=$(mktemp -d /opt/trud-release-check-20261019.XXXXXXXX)
chown trudsite:trudsite "$WORK"
runuser -u trudsite -- git init --bare --quiet "$WORK/repository.git"
# Не меняем FETCH_HEAD/objects работающего checkout даже в --check.
printf '%s\n' "$APP/.git/objects" > "$WORK/repository.git/objects/info/alternates"
chown trudsite:trudsite "$WORK/repository.git/objects/info/alternates"
# Временный репозиторий не знает deploy-ключ: берём core.sshCommand рабочего checkout.
SSHCMD=$(gitapp config --get core.sshCommand)
[[ -n $SSHCMD ]]
gitstage -c core.sshCommand="$SSHCMD" fetch --quiet --no-tags git@github.com:Zursulus/trud-1.git refs/heads/claude/redesign-2026 >"$WORK/fetch.log" 2>&1 \
  || { cat "$WORK/fetch.log" >&2; false; }
[[ $(gitstage rev-parse FETCH_HEAD) == "$TARGET_SHA" ]]
gitstage merge-base --is-ancestor "$EXPECTED_SHA" "$TARGET_SHA"
[[ -z $(gitstage diff --name-only "$EXPECTED_SHA" "$TARGET_SHA" -- \
    backend/requirements.txt ':(glob)**/requirements*.txt' ':(glob)**/pyproject.toml' \
    ':(glob)**/*.lock' ':(glob)**/package-lock.json' ':(glob)**/npm-shrinkwrap.json' \
    ':(glob)**/Pipfile*' ':(glob)**/setup.py' ':(glob)**/setup.cfg' ':(glob)**/package.json' ':(glob)backend/**/migrations/**' \
    ':(glob)backend/**/management/**' ops/backup-trud-site.sh ops/trud-1-backup.service ops/trud-1-backup.timer) ]]
gitstage diff --stat "$EXPECTED_SHA" "$TARGET_SHA"
mkdir "$WORK/source"
gitstage archive "$TARGET_SHA" | tar -xf - -C "$WORK/source"
chown -R trudsite:trudsite "$WORK/source"
ok
step 'Staging: check, makemigrations, пустой migrate --plan, форум и UTF8'
manage_at "$WORK/source" check >"$WORK/check.log" 2>&1
manage_at "$WORK/source" makemigrations --check --dry-run >"$WORK/makemigrations.log" 2>&1
empty_plan "$WORK/source" >"$WORK/plan.log" 2>&1
manage_at "$WORK/source" shell --no-imports -c 'from django.conf import settings; from django.db import connection; assert not settings.DEBUG and settings.FORUM_ENABLED and not settings.RESIDENT_EMAIL_LOGIN_ENABLED and not settings.FORUM_PUBLIC_READ; c=connection.cursor(); c.execute("SHOW server_encoding"); assert c.fetchone()[0]=="UTF8"' >"$WORK/flags.log" 2>&1
printf 'TRUD scanner readiness probe\n' | runuser -u trudsite -- /usr/bin/clamdscan --stream --no-summary - >"$WORK/scanner.log" 2>&1
ok
step 'Поддержка атомарного обмена на публичном filesystem'
PROBE=$(mktemp -d /var/www/trud-1/releases/.trud-exchange-probe.XXXXXXXX)
mkdir "$PROBE/a" "$PROBE/b"
exchange "$PROBE/a" "$PROBE/b"
exchange "$PROBE/a" "$PROBE/b"
rmdir "$PROBE/a" "$PROBE/b" "$PROBE"
PROBE=''
ok
if [[ $MODE == --check ]]; then
    printf 'ИТОГ: проверки OK; production не изменён. BACKUP=не создаётся в --check\n'
    exit 0
fi
step 'Свежий штатный backup и подтверждение восстановления текущего запуска'
BACKUP_STATE=$(systemctl show trud-1-backup.service -p ActiveState --value)
[[ $BACKUP_STATE == inactive || $BACKUP_STATE == failed ]]
OLD_STARTED=$(systemctl show trud-1-backup.service -p ExecMainStartTimestampMonotonic --value)
CURSOR=$(journalctl -u trud-1-backup.service -n 1 --show-cursor --no-pager -o cat | sed -n 's/^-- cursor: //p')
[[ -n $CURSOR ]]
systemctl start trud-1-backup.service
STARTED=$(systemctl show trud-1-backup.service -p ExecMainStartTimestampMonotonic --value)
[[ $STARTED =~ ^[0-9]+$ && $STARTED != 0 && $STARTED != "$OLD_STARTED" ]]
[[ $(systemctl show trud-1-backup.service -p Result --value) == success ]]
[[ $(systemctl show trud-1-backup.service -p ExecMainStatus --value) == 0 ]]
VERIFIED_BACKUP=$(journalctl -u trud-1-backup.service --after-cursor="$CURSOR" --no-pager -o cat | python3 -c 'import pathlib,re,sys; a=re.findall(r"^Резервная копия проверена контрольным восстановлением: (/var/backups/trud-1-daily/[0-9TZ-]+)$",sys.stdin.read(),re.M); assert len(a)==1 and pathlib.Path(a[0]).is_dir(); print(a[0])')
exec 9>/run/trud-backup.lock
flock -n 9
BACKUP=$(mktemp -d /var/backups/trud-release-20261019.XXXXXXXX)
printf 'BACKUP=%s\n' "$BACKUP"
printf '%s\n' "$VERIFIED_BACKUP" > "$BACKUP/verified-daily-backup.txt"
[[ $(baseline) == "$EXPECTED_SHA" && -z $(gitapp status --porcelain) ]]
ok
step 'Архивы старого релиза, кода, статики и marker; готовый ручной откат'
tar -C / -czf "$BACKUP/current-release.tar.gz" "${PUBLIC_ROOT#/}" var/lib/trud-1/deployment-status.json etc/nginx/sites-enabled/trud-1
gitapp archive "$EXPECTED_SHA" > "$BACKUP/previous-code.tar"
[[ -d $APP/backend/staticfiles && ! -L $APP/backend/staticfiles ]]
tar -C "$APP/backend" -czf "$BACKUP/staticfiles.tar.gz" staticfiles
cp -p "$STATUS" "$BACKUP/deployment-status.json"
for archive in current-release staticfiles; do tar -tzf "$BACKUP/$archive.tar.gz" >/dev/null; done
tar -tf "$BACKUP/previous-code.tar" >/dev/null
gitstage show "$TARGET_SHA:ops/rollback-redesign-20261019.sh" > "$BACKUP/rollback.sh"
chmod 700 "$BACKUP/rollback.sh"
bash -n "$BACKUP/rollback.sh"
ok
step 'Подготовка публичных файлов и проверка атомарного exchange'
PUBLIC_STAGE=$(mktemp -d /var/www/trud-1/releases/.trud-redesign-20261019.XXXXXXXX)
cp -a "$PUBLIC_ROOT/." "$PUBLIC_STAGE/"
PUBLIC_FILES=(index.html app.js style.css public-content.css feodosia-letter-2026-08-27.html feodosia-letter-2026-08-27.webp landscape.webp ordzhonikidze-sunset.webp)
for file in "${PUBLIC_FILES[@]}"; do
    rm -f -- "$PUBLIC_STAGE/$file"
    gitstage show "$TARGET_SHA:$file" > "$PUBLIC_STAGE/$file"
    [[ -s $PUBLIC_STAGE/$file ]]; chmod 644 "$PUBLIC_STAGE/$file"
done
chmod 755 "$PUBLIC_STAGE"
PUBLIC_OLD_ID=$(stat -c '%d:%i' "$PUBLIC_ROOT")
PUBLIC_NEW_ID=$(stat -c '%d:%i' "$PUBLIC_STAGE")
printf 'EXPECTED_SHA=%q\nTARGET_SHA=%q\nPUBLIC_ROOT=%q\nPUBLIC_STAGE=%q\nPUBLIC_OLD_ID=%q\nPUBLIC_NEW_ID=%q\n' "$EXPECTED_SHA" "$TARGET_SHA" "$PUBLIC_ROOT" "$PUBLIC_STAGE" "$PUBLIC_OLD_ID" "$PUBLIC_NEW_ID" > "$BACKUP/state.sh"
# До первой мутации уже есть проверенный комплект для возврата кода и статики.
(cd "$BACKUP" && sha256sum current-release.tar.gz previous-code.tar staticfiles.tar.gz deployment-status.json state.sh rollback.sh > SHA256SUMS && sha256sum --check --status SHA256SUMS)
[[ $(baseline) == "$EXPECTED_SHA" && -z $(gitapp status --porcelain) ]]
ok
step 'Остановка приложения и холодный снимок БД и private-data'
CHANGING=1
systemctl stop trud-1-site.service
runuser -u postgres -- pg_dump -Fc trud_site > "$BACKUP/trud_site.dump"
pg_restore --list "$BACKUP/trud_site.dump" >/dev/null
tar -C "$APP" -czf "$BACKUP/private-data.tar.gz" private-data
tar -tzf "$BACKUP/private-data.tar.gz" >/dev/null
# Отдельно: частичный холодный снимок не должен блокировать откат кода.
(cd "$BACKUP" && sha256sum trud_site.dump private-data.tar.gz > COLD-SHA256SUMS && sha256sum --check --status COLD-SHA256SUMS)
ok
step 'Переключение кода, check, пустой план миграций и collectstatic'
# Объекты переносим из проверенного staging; сеть в окне остановки не нужна.
gitapp fetch --quiet --no-tags "$WORK/repository.git" "$TARGET_SHA" >"$BACKUP/import.log" 2>&1
gitapp checkout --detach "$TARGET_SHA" >"$BACKUP/checkout.log" 2>&1
manage_at "$APP" check >"$BACKUP/check.log" 2>&1
empty_plan "$APP" >"$BACKUP/plan.log" 2>&1
manage_at "$APP" collectstatic --noinput >"$BACKUP/collectstatic.log" 2>&1
ok
step 'Атомарная публикация публичных файлов и проверка bytes'
[[ $(stat -c '%d:%i' "$PUBLIC_ROOT") == "$PUBLIC_OLD_ID" && $(stat -c '%d:%i' "$PUBLIC_STAGE") == "$PUBLIC_NEW_ID" ]]
exchange "$PUBLIC_ROOT" "$PUBLIC_STAGE"
for file in "${PUBLIC_FILES[@]}"; do
    cmp "$PUBLIC_ROOT/$file" "$WORK/source/$file"
    curl --connect-timeout 3 --max-time 15 -fsS "https://trud-1.ru/$file?release=$TARGET_SHA" | cmp - "$WORK/source/$file"
done
ok
step 'Рестарт и smoke главной, кабинета, профиля, work и ресурсов'
systemctl restart trud-1-site.service
for unit in trud-1-site.service nginx.service postgresql.service; do systemctl is-active --quiet "$unit"; done
python3 - <<'PY'
import subprocess,time
for path,wants in [('/',(200,)),('/admin/',(200,302)),('/admin/cabinet/login/',(200,)),('/admin/cabinet/',(302,)),('/admin/cabinet/forum/',(302,)),('/admin/cabinet/profile/',(302,)),('/work/',(302,)),('/accounts/signup/',(404,)),('/accounts/login/',(404,)),('/admin/cabinet/accounts/signup/',(404,)),('/admin/cabinet/accounts/login/',(404,))]:
 for attempt in range(10):
  r=subprocess.check_output(['curl','--connect-timeout','3','--max-time','15','-sS','-o','/dev/null','-w','%{http_code}','https://trud-1.ru'+path],text=True)
  if r not in ('502','503'): break
  time.sleep(1)
 assert int(r) in wants, (path,r)
PY
for file in internal-nav.css sea-sunset.css internal-nav.js; do
    curl --connect-timeout 3 --max-time 10 -fsS "https://trud-1.ru/admin-static/water/$file" -o /dev/null
done
ok
step 'Атомарный marker и подтверждение TARGET_SHA через HTTP'
python3 - "$TARGET_SHA" <<'PY'
import datetime,grp,json,os,sys,tempfile
fd,tmp=tempfile.mkstemp(prefix='deployment-status.',dir='/var/lib/trud-1')
with os.fdopen(fd,'w') as f:
 json.dump({'project':'trud-1','commit':sys.argv[1],'deployed_at':datetime.datetime.now(datetime.timezone.utc).isoformat()},f); f.write('\n'); f.flush(); os.fsync(f.fileno())
os.chown(tmp,0,grp.getgrnam('trudsite').gr_gid); os.chmod(tmp,0o640)
os.replace(tmp,'/var/lib/trud-1/deployment-status.json')
PY
# По требованию этого скрипта даже ошибка после marker вызывает откат.
curl --retry 5 --retry-delay 1 --retry-connrefused --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/admin/deployment-status/ | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p["project"]=="trud-1" and p["commit"]==sys.argv[1]' "$TARGET_SHA"
[[ $(gitapp rev-parse HEAD) == "$TARGET_SHA" && -z $(gitapp status --porcelain) ]]
ok
trap - ERR INT TERM
printf 'ИТОГ: релиз %s установлен, smoke OK. BACKUP=%s\n' "$TARGET_SHA" "$BACKUP"
printf 'Ручной откат: bash %q/rollback.sh %q\n' "$BACKUP" "$BACKUP"
