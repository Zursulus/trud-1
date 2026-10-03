#!/bin/bash
set -Eeuo pipefail
grep -Fq '"deploy-trud-compatible"' /usr/local/sbin/system-maintenance-worker
test -x /usr/local/sbin/deploy-trud-compatible
bash -n /usr/local/sbin/deploy-trud-compatible
echo 'TRUD_DEPLOY_BROKER_SMOKE=PASS'
