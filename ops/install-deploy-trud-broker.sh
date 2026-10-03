#!/bin/bash
set -Eeuo pipefail
umask 077

WORKER=/usr/local/sbin/system-maintenance-worker
HELPER=/usr/local/sbin/deploy-trud-compatible
SUBMIT=/usr/local/bin/system-maintenance-submit
SRC="$(cd "$(dirname "$0")" && pwd)"

[[ $(id -u) -eq 0 ]] || { echo 'root required' >&2; exit 2; }
install -o root -g root -m 0700 "$SRC/deploy-trud-compatible.sh" "$HELPER"

python3 - "$WORKER" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
s=p.read_text()
if '"deploy-trud-compatible"' not in s:
    s=s.replace(
        'ACTIONS={"debian13-upgrade","apt-current-upgrade","reboot-host","post-upgrade-finalize"}',
        'ACTIONS={"debian13-upgrade","apt-current-upgrade","reboot-host","post-upgrade-finalize","deploy-trud-compatible"}',
    )
    marker='        elif req["action"]=="reboot-host":\n            unit=launch_reboot()'
    replacement=marker+'\n        elif req["action"]=="deploy-trud-compatible":\n            unit=launch_exact(["/usr/local/sbin/deploy-trud-compatible"], "trud-deploy")'
    if marker not in s:
        raise SystemExit('worker dispatch marker not found')
    s=s.replace(marker,replacement)
p.write_text(s)
PY
chmod 0700 "$WORKER"
systemctl daemon-reload
systemctl restart system-maintenance.path
echo 'TRUD_DEPLOY_BROKER_INSTALL=PASS'
