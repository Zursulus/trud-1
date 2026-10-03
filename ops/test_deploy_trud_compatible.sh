#!/bin/bash
set -Eeuo pipefail
f=ops/deploy-trud-compatible.sh
bash -n "$f"
grep -Fq 'EXPECTED_LIVE_SHA' "$f"
grep -Fq 'TARGET_SHA' "$f"
grep -Fq 'DEPLOY_SCRIPT_SHA256' "$f"
grep -Fq 'gitapp merge-base --is-ancestor' "$f"
grep -Fq 'sha256sum -c -' "$f"
grep -Fq 'bash "$tmp" "$TARGET_SHA" "$EXPECTED_LIVE_SHA"' "$f"
! grep -Eq 'eval|sudo|force-push|reset --hard' "$f"
echo 'deploy broker helper static guards: PASS'
