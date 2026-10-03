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
: "${RELEASE_KIND:?}"
: "${MIGRATION_INTENT:?}"
: "${SETUP_ROLES_INTENT:?}"
: "${SCANNER_INTENT:?}"

[[ "$RELEASE_KIND" == "security-alert-0034" ]]
[[ "$MIGRATION_INTENT" == "water:0034_security_alert" ]]
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
bash "$tmp" "$TARGET_SHA" "$EXPECTED_LIVE_SHA"
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
