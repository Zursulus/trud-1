#!/bin/bash
# Ручной откат: bash rollback-redesign-20261019.sh /var/backups/trud-release-20261019.XXXXXXXX
# БД и private-data намеренно не восстанавливаются: новые записи сохраняются.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C.UTF-8
STEP=0 STOPPED=0
BACKUP=${1:-}
fail() { trap - ERR INT TERM; if [[ $STOPPED == 1 ]]; then systemctl stop trud-1-site.service >/dev/null 2>&1 || true; fi; printf 'ШАГ %s: ОШИБКА. Откат не подтверждён; BACKUP=%s\n' "$STEP" "$BACKUP" >&2; exit 1; }
trap fail ERR INT TERM
step() { STEP=$((STEP+1)); printf 'ШАГ %s: %s\n' "$STEP" "$1"; }
ok() { printf 'ШАГ %s: OK\n' "$STEP"; }
if [[ $# != 1 || ! $BACKUP =~ ^/var/backups/trud-release-20261019\.[A-Za-z0-9]+$ ]]; then
    echo 'Использование: bash rollback-redesign-20261019.sh <BACKUP_DIR>' >&2; exit 2
fi
[[ $(id -u) == 0 ]]
step 'Проверка закрытого снимка и блокировки'
# При автоматическом откате fd 9 наследует уже взятую блокировку.
if [[ ! /proc/$$/fd/9 -ef /run/trud-backup.lock ]]; then exec 9>/run/trud-backup.lock; fi
flock -n 9
[[ -d $BACKUP && ! -L $BACKUP && $(stat -c %u "$BACKUP") == 0 ]]
[[ $(stat -c %a "$BACKUP") == 700 ]]
for f in state.sh SHA256SUMS staticfiles.tar.gz deployment-status.json; do
    [[ -f $BACKUP/$f && ! -L $BACKUP/$f && $(stat -c %u "$BACKUP/$f") == 0 ]]
    mode=$(stat -c %a "$BACKUP/$f"); (( (8#$mode & 0022) == 0 ))
done
(cd "$BACKUP" && sha256sum --check --status SHA256SUMS)
# Только созданный release-скриптом root-only state; секретов здесь нет.
# shellcheck disable=SC1091
source "$BACKUP/state.sh"
: "${EXPECTED_SHA:?}" "${TARGET_SHA:?}" "${PUBLIC_ROOT:?}" "${PUBLIC_STAGE:?}" "${PUBLIC_OLD_ID:?}" "${PUBLIC_NEW_ID:?}"
[[ $EXPECTED_SHA == 8b15614f64a5a7b2b164cf865bb409e96ca2a1cb && $TARGET_SHA =~ ^[0-9a-f]{40}$ ]]
[[ $PUBLIC_ROOT == /var/www/trud-1/releases/bef1cb7 && $PUBLIC_STAGE == /var/www/trud-1/releases/.trud-redesign-20261019.* ]]
python3 - "$EXPECTED_SHA" "$TARGET_SHA" "$PUBLIC_ROOT" "$PUBLIC_STAGE" "$PUBLIC_OLD_ID" "$PUBLIC_NEW_ID" <<'PY'
import json,os,subprocess,sys
old,new,root,stage,old_id,new_id=sys.argv[1:]
head=subprocess.check_output(['runuser','-u','trudsite','--','git','-C','/opt/trud-1-site','rev-parse','HEAD'],text=True).strip()
assert head in (old,new), 'Другой релиз: HEAD'
assert json.load(open('/var/lib/trud-1/deployment-status.json'))['commit'] in (old,new), 'Другой релиз: marker'
def ident(p):
 assert not os.path.islink(p)
 s=os.stat(p); return f'{s.st_dev}:{s.st_ino}'
assert (ident(root),ident(stage)) in ((old_id,new_id),(new_id,old_id)), 'Другие публичные каталоги'
PY
ok
step 'Остановка сервиса и возврат кода и точной статики'
STOPPED=1
systemctl stop trud-1-site.service
runuser -u trudsite -- git -C /opt/trud-1-site checkout --detach "$EXPECTED_SHA" >/dev/null 2>&1
systemd-run --quiet --wait --pipe --collect --property=User=trudsite --property=Group=trudsite --property=WorkingDirectory=/opt/trud-1-site/backend --property=EnvironmentFile=/etc/trud-1-site.env --setenv=PGOPTIONS=-cdefault_transaction_read_only=on /opt/trud-1-site/.venv/bin/python /opt/trud-1-site/backend/manage.py check >"$BACKUP/rollback-check.log" 2>&1
[[ ! -L /opt/trud-1-site/backend/staticfiles ]]
# Удаляем добавленную новым релизом статику; архив проверен до остановки.
rm -rf -- /opt/trud-1-site/backend/staticfiles
tar -xzf "$BACKUP/staticfiles.tar.gz" -C /opt/trud-1-site/backend
ok
step 'Атомарный возврат публичного каталога и marker'
python3 - "$PUBLIC_ROOT" "$PUBLIC_STAGE" "$PUBLIC_OLD_ID" "$PUBLIC_NEW_ID" "$BACKUP" <<'PY'
import ctypes,os,shutil,sys,tempfile
root,stage,old,new,backup=sys.argv[1:]
def ident(p):
 s=os.stat(p,follow_symlinks=False); return f'{s.st_dev}:{s.st_ino}'
pair=ident(root),ident(stage)
if pair != (old,new):
 assert pair == (new,old), 'Идентичности каталогов изменились'
 f=ctypes.CDLL(None,use_errno=True).renameat2
 f.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
 f.restype=ctypes.c_int
 if f(-100,os.fsencode(root),-100,os.fsencode(stage),2):
  n=ctypes.get_errno(); raise OSError(n,os.strerror(n))
fd,tmp=tempfile.mkstemp(prefix='deployment-status.',dir='/var/lib/trud-1'); os.close(fd)
shutil.copy2(backup+'/deployment-status.json',tmp)
s=os.stat(backup+'/deployment-status.json'); os.chown(tmp,s.st_uid,s.st_gid)
os.replace(tmp,'/var/lib/trud-1/deployment-status.json')
PY
ok
step 'Запуск старого релиза и smoke'
systemctl start trud-1-site.service
systemctl is-active --quiet trud-1-site.service
curl --retry 5 --retry-delay 1 --retry-connrefused --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/admin/deployment-status/ | python3 -c 'import json,sys; p=json.load(sys.stdin); assert p["project"]=="trud-1" and p["commit"]==sys.argv[1]' "$EXPECTED_SHA"
[[ $(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/) == 200 ]]
admin=$(curl --connect-timeout 3 --max-time 10 -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/admin/)
[[ $admin == 200 || $admin == 302 ]]
ok
STOPPED=0
trap - ERR INT TERM
printf 'ИТОГ: откат подтверждён. BACKUP=%s\n' "$BACKUP"
