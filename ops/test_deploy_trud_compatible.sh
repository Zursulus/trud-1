#!/bin/bash
set -Eeuo pipefail
wrapper=ops/deploy-trud-compatible.sh
release=ops/deploy-migration-aware.sh
bash -n "$wrapper" "$release"
for token in EXPECTED_LIVE_SHA TARGET_SHA BRANCH DEPLOY_SCRIPT_SHA256 RELEASE_KIND MIGRATION_INTENT SETUP_ROLES_INTENT SCANNER_INTENT; do
    grep -Fq "$token" "$wrapper"
done
grep -Fq 'gitapp merge-base --is-ancestor' "$wrapper"
grep -Fq 'sha256sum -c -' "$wrapper"
grep -Fq 'ops/deploy-migration-aware.sh' "$wrapper"
grep -Fq 'water:0034_security_alert' "$wrapper"
grep -Fq 'DEPLOY_TRUD_COMPATIBLE=COMMITTED_POSTCHECK_FAILED' "$wrapper"
grep -Fq 'backend/water/migrations/0034_security_alert.py' "$release"
grep -Fq 'manage migrate --noinput' "$release"
grep -Fq 'manage setup_roles' "$release"
grep -Fq 'scanner_ready' "$release"
grep -Fq 'DEPLOY_MIGRATION_AWARE=COMMITTED_POSTCHECK_FAILED' "$release"
! grep -Eq 'eval|sudo|force-push|reset --hard' "$wrapper"
echo 'deploy broker migration-aware guards: PASS'
