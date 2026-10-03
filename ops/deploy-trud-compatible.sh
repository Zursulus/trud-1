#!/bin/bash
set -Eeuo pipefail
umask 077

MANIFEST=/etc/system-maintenance/trud-release.conf
APP=/opt/trud-1-site
STATUS=/var/lib/trud-1/deployment-status.json

[[ $(id -u) -eq 0 ]] || { echo 'root required' >&2; exit 2; }
[[ -r "$MANIFEST" ]] || { echo 'release manifest missing' >&2; exit 2; }

# Root-owned manifest contains shell-quoted scalar assignments only.
# shellcheck disable=SC1090
source "$MANIFEST"
: "${EXPECTED_LIVE_SHA:?}"
: "${TARGET_SHA:?}"
: "${BRANCH:?}"
: "${DEPLOY_SCRIPT_SHA256:?}"

gitapp(){ runuser -u trudsite -- git -C "$APP" "$@"; }

live="$(python3 - "$STATUS" <<'PY'
import json,sys
print(json.load(open(sys.argv[1],encoding='utf-8'))['commit'])
PY
)"
[[ "$live" == "$EXPECTED_LIVE_SHA" ]]
[[ -z "$(gitapp status --porcelain)" ]]
[[ "$(gitapp rev-parse HEAD)" == "$EXPECTED_LIVE_SHA" ]]

gitapp fetch --no-tags origin "refs/heads/$BRANCH"
fetched="$(gitapp rev-parse FETCH_HEAD)"
[[ "$fetched" == "$TARGET_SHA" ]]
gitapp cat-file -e "$TARGET_SHA^{commit}"
gitapp merge-base --is-ancestor "$EXPECTED_LIVE_SHA" "$TARGET_SHA"

tmp="$(mktemp /var/backups/trud-deploy-helper.XXXXXXXX)"
trap 'rm -f "$tmp"' EXIT
gitapp show "$TARGET_SHA:ops/deploy-compatible.sh" >"$tmp"
printf '%s  %s\n' "$DEPLOY_SCRIPT_SHA256" "$tmp" | sha256sum -c -
chmod 700 "$tmp"
bash "$tmp" "$TARGET_SHA" "$EXPECTED_LIVE_SHA"

post="$(curl --connect-timeout 3 --max-time 10 -fsS https://trud-1.ru/admin/deployment-status/ | python3 -c 'import json,sys; print(json.load(sys.stdin)["commit"])')"
[[ "$post" == "$TARGET_SHA" ]]
for s in trud-1-site.service nginx.service postgresql@17-main.service; do systemctl is-active --quiet "$s"; done
[[ "$(curl -sS -o /dev/null -w '%{http_code}' https://trud-1.ru/)" == 200 ]]
echo "DEPLOY_TRUD_COMPATIBLE=PASS"
echo "target=$TARGET_SHA"
