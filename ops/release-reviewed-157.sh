#!/bin/bash
# One-time administrator command: verified backup, broker bootstrap, site release.
set -Eeuo pipefail
umask 077
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export SYSTEMD_PAGER=cat
[[ $(id -u) == 0 && $# == 0 ]] || { echo 'root, no arguments required' >&2; exit 2; }
EXPECTED=27f62efcb8381e34f3de895c9fc41d1613c9cfd0
TARGET=7e685dfa6a331a433e916a62b1d555fe04847c4c
SOURCE=677a05184fa7b4ddaeb1a491d9ce4332e68ee911
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json
gitapp() { runuser -u trudsite -- git -C "$APP" "$@"; }
[[ $(gitapp rev-parse HEAD) == "$EXPECTED" ]]
[[ -z $(gitapp status --porcelain) ]]
python3 - "$STATUS" "$EXPECTED" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
assert m.get('project') == 'trud-1' and m.get('commit') == sys.argv[2], 'Production drift'
PY
for unit in trud-1-site.service nginx.service postgresql@17-main.service; do
    systemctl is-active --quiet "$unit"
done
nginx -t -q
# Conservative budget includes isolated restore, daily backup, release backup,
# staged public assets and a reserve. Never delete backups to make room.
db_bytes=$(runuser -u postgres -- psql --no-psqlrc -At -d postgres -c "SELECT pg_database_size('trud_site');")
private_bytes=0
[[ ! -d "$APP/private-data" ]] || private_bytes=$(du -sb "$APP/private-data" | cut -f1)
public_bytes=$(du -sb /var/www/trud-1/releases | cut -f1)
free_bytes=$(df -B1 --output=avail /var/backups | tail -1 | tr -d ' ')
[[ $db_bytes =~ ^[0-9]+$ && $private_bytes =~ ^[0-9]+$ && $public_bytes =~ ^[0-9]+$ && $free_bytes =~ ^[0-9]+$ ]]
required=$((4 * db_bytes + 2 * private_bytes + 2 * public_bytes + 536870912))
((free_bytes >= required)) || { echo "SPACE_BLOCKED available=$free_bytes required=$required" >&2; exit 2; }
# Test the real scanner as the application user, without storing a test file.
printf 'TRUD clean scanner probe\n' | runuser -u trudsite -- /usr/bin/clamdscan --stream --no-summary -
set +e
printf '%s' 'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' |
    runuser -u trudsite -- /usr/bin/clamdscan --stream --no-summary -
scanner_rc=$?
set -e
[[ $scanner_rc == 1 ]] || { echo 'Scanner did not reject the standard test signature' >&2; exit 2; }

STAGE=$(mktemp -d /root/trud-release-157.XXXXXXXX)
echo "RELEASE_STAGE=$STAGE"
for file in deploy-trud-compatible.sh install-deploy-trud-broker.sh smoke-deploy-trud-broker.sh trud-release-157.conf backup-trud-site.sh; do
    curl --fail --location --connect-timeout 10 --max-time 60 \
        "https://raw.githubusercontent.com/Zursulus/trud-1/$SOURCE/ops/$file" --output "$STAGE/$file"
done
cd "$STAGE"
sha256sum -c - <<'SUMS'
904ccd220cecb97f487dbcb8ca4011df7e5feb8c7cf8de2ec24e882ac1acd836  deploy-trud-compatible.sh
d7b4deaa6c88b043356396c9beb6ca351c2ebbad630274807e40f33184ff7c86  install-deploy-trud-broker.sh
ea005839da4d4cecb70920cbebd7a9b373cb1ff05c0c965414014e050ac66b24  smoke-deploy-trud-broker.sh
89fe78a9b4afe6b55b48558207d34dd2bb8ac8bd931f427c56e7e1c1fc02d0ed  trud-release-157.conf
790be71b5d37332e542a424e8a2ca58bb24a732fec2f7991b2010a6dce70649b  backup-trud-site.sh
SUMS
bash -n deploy-trud-compatible.sh install-deploy-trud-broker.sh smoke-deploy-trud-broker.sh backup-trud-site.sh
# Dedicated new backup root: retention cannot remove any existing backup.
BACKUP_ROOT=$(mktemp -d /var/backups/trud-1-release-157.XXXXXXXX)
TRUD_BACKUP_ROOT="$BACKUP_ROOT" bash ./backup-trud-site.sh
echo "VERIFIED_BACKUP_ROOT=$BACKUP_ROOT"
bash ./install-deploy-trud-broker.sh
bash ./smoke-deploy-trud-broker.sh
# Synchronous fixed broker gives the administrator the real exit status.
# Ordinary subsequent releases use the installed maintenance queue command.
/usr/local/sbin/deploy-trud-compatible
echo "RELEASE_157=PASS target=$TARGET"
curl --fail --connect-timeout 3 --max-time 10 https://trud-1.ru/admin/deployment-status/
echo
