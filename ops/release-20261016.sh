#!/bin/bash
# Выкатка релиза 20261016 на production. Запуск под root: bash release-20261016.sh КОМАНДА
#   admins  — список админов: логин, последний вход, есть ли 2FA (только чтение)
#   prepare — бэкап настроек, allauth, установка broker, флаги env, манифест (сервис не трогает)
#   deploy  — штатная выкатка /usr/local/sbin/deploy-trud-compatible (бэкап БД внутри)
#   verify  — проверка после выкатки
set -Eeuo pipefail
LIVE=6fb32eff0f389eda944371d802cd14dec8eb2adb
TARGET=4a856c4c16f379e59b1dc6106a6c33367687caa9
BRANCH=claude/prod-release
HELPER_SHA=538a2ee7d94479b6618e8e36111f8c393ff91e8de4329d8aea7d616583e6de31
MIGR='account:0001_initial,account:0002_email_max_length,account:0003_alter_emailaddress_create_unique_verified_email,account:0004_alter_emailaddress_drop_unique_email,account:0005_emailaddress_idx_upper_email,account:0006_emailaddress_lower,account:0007_emailaddress_idx_email,account:0008_emailaddress_unique_primary_email_fixup,account:0009_emailaddress_unique_primary_email,forum:0001_initial,public_site:0002_publicimage_alter_publicnews_is_featured_and_more'
APP=/opt/trud-1-site; ENV=/etc/trud-1-site.env; CONF=/etc/system-maintenance/trud-release.conf
ok(){ echo "  ✔ $*"; }; die(){ echo "  ✘ $*" >&2; exit 1; }
[[ $(id -u) -eq 0 ]] || die "нужен root"
g(){ runuser -u trudsite -- git -C "$APP" "$@"; }
dj(){ systemd-run --quiet --wait --pipe --collect --property=User=trudsite --property=Group=trudsite \
  --property=WorkingDirectory=$APP/backend --property=EnvironmentFile=$ENV \
  --setenv=PGOPTIONS=-cdefault_transaction_read_only=on $APP/.venv/bin/python $APP/backend/manage.py shell --no-imports -c "$1"; }
check_live(){
  g fetch -q --no-tags origin "refs/heads/$BRANCH"
  [[ "$(g rev-parse FETCH_HEAD)" == "$TARGET" ]] || die "ветка $BRANCH не на $TARGET"; ok "TARGET на GitHub совпадает"
  [[ -z "$(g status --porcelain)" ]] || die "рабочее дерево не чистое"; ok "дерево чистое"
  [[ "$(g rev-parse HEAD)" == "$LIVE" ]] || die "на проде не $LIVE"; ok "на проде $LIVE"
  g merge-base --is-ancestor "$LIVE" "$TARGET" || die "TARGET не потомок LIVE"; ok "TARGET — потомок LIVE"; }
case "${1:-}" in
admins)
  dj '
from django.contrib.auth import get_user_model
from django.db.models import Exists, OuterRef, Q
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice
U=get_user_model()
qs=U.objects.filter(is_active=True).filter(Q(is_superuser=True)|Q(groups__name__in=["Администратор ТСН","Администратор СНТ"])).distinct().annotate(
 t=Exists(TOTPDevice.objects.filter(user_id=OuterRef("pk"),confirmed=True)),s=Exists(StaticDevice.objects.filter(user_id=OuterRef("pk"),confirmed=True)))
for u in qs.order_by("username"):
    print(f"{u.username:24} вход: {u.last_login.date() if u.last_login else \"никогда\"}  суперпользователь: {\"да\" if u.is_superuser else \"нет\"}  2FA: {\"есть\" if (u.t or u.s) else \"НЕТ\"}")
' ;;
prepare)
  echo "1/6 проверки"; check_live
  echo "2/6 бэкап настроек"; B=/var/backups/trud-release-20261016-config; install -d -o root -g root -m 0700 $B
  for f in $ENV /usr/local/sbin/deploy-trud-compatible $CONF /var/lib/trud-1/deployment-status.json; do [[ -e $f ]] && cp -an --preserve=all "$f" $B/; done; ok "сохранено в $B"
  echo "3/6 allauth"; runuser -u trudsite -- $APP/.venv/bin/python -m pip install -q --no-deps django-allauth==65.18.0
  runuser -u trudsite -- $APP/.venv/bin/python -c 'from importlib.metadata import version as v; assert v("django-allauth")=="65.18.0"' && ok "django-allauth 65.18.0"
  echo "4/6 broker из TARGET"; T=$(mktemp /root/trud-broker.XXXX); g show "$TARGET:ops/deploy-compatible.sh" > "$T"; bash -n "$T"
  install -o root -g root -m 0700 "$T" /usr/local/sbin/deploy-trud-compatible; rm -f "$T"; ok "deploy-trud-compatible обновлён"
  echo "5/6 флаги env"; python3 - "$ENV" <<'PY'
import sys; from pathlib import Path
p=Path(sys.argv[1]); fl={'TRUD_FORUM_ENABLED':'1','TRUD_RESIDENT_EMAIL_LOGIN_ENABLED':'0','TRUD_FORUM_PUBLIC_READ':'0'}
l=[x for x in p.read_text().splitlines() if x.split('=',1)[0].strip() not in fl]
p.write_text('\n'.join(l+[f'{k}={v}' for k,v in fl.items()])+'\n')
PY
  ok "форум=1, вход по почте=0, публичное чтение=0"
  echo "6/6 манифест"; [[ "$(g show "$TARGET:ops/deploy-migration-aware.sh" | sha256sum | cut -c1-64)" == "$HELPER_SHA" ]] || die "sha256 помощника не совпал"; ok "sha256 помощника совпал"
  install -d -o root -g root -m 0700 /etc/system-maintenance
  (umask 077; printf "%s\n" "EXPECTED_LIVE_SHA='$LIVE'" "TARGET_SHA='$TARGET'" "BRANCH='$BRANCH'" "DEPLOY_SCRIPT_SHA256='$HELPER_SHA'" \
    "RELEASE_KIND='release-20261016'" "MIGRATION_INTENT='$MIGR'" "SETUP_ROLES_INTENT='required'" "SCANNER_INTENT='required-before-start'" > $CONF.next)
  chown root:root $CONF.next; chmod 0600 $CONF.next; mv -f $CONF.next $CONF; ok "манифест записан"
  echo "ГОТОВО: можно запускать deploy" ;;
deploy) check_live; /usr/local/sbin/deploy-trud-compatible ;;
verify)
  [[ "$(g rev-parse HEAD)" == "$TARGET" ]] && ok "на проде $TARGET" || die "HEAD не TARGET"
  systemctl is-active --quiet trud-1-site nginx postgresql@17-main && ok "службы active" || die "служба не active"
  curl -fsS -m 10 https://trud-1.ru/admin/deployment-status/ | grep -q "$TARGET" && ok "marker = TARGET" || die "marker не TARGET"
  for u in / /admin/login/ /admin/cabinet/forum/ /admin/cabinet/accounts/signup/; do echo "  $u → $(curl -s -o /dev/null -m 10 -w '%{http_code}' https://trud-1.ru$u)"; done
  echo "  ожидание: / 200, /admin/login/ 200, форум 302 (на вход), signup 404" ;;
*) sed -n '2,7p' "$0"; exit 2 ;;
esac
