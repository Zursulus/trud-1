# AGENTS.md — Труд-1

## Purpose

Production-backed site and Django workspace for ТСН «ТРУД-1». Prefer the smallest reversible change that satisfies the active work item. Public site, staff workspace/admin and resident cabinet are separate surfaces and must not be assumed to share deployment behavior.

## Sources of truth

Before substantial work use this order:

1. Active GitHub Issue / PR — current unfinished obligation, scope and acceptance criteria.
2. `docs/PLAN.md` — product roadmap, architecture phase and permanent project invariants.
3. `backend/README.md` — data-model, runtime and safety invariants.
4. `ops/DEPLOYMENT.md` and `ops/RECOVERY.md` when deployment/recovery is in scope.
5. Production deployment marker/status — only source of the actually installed commit.
6. Notion — durable decisions/rationale and a short recovery checkpoint; not a duplicate task list.
7. Linear — read-only historical archive for pre-migration work. Do not create new project work there.

If sources disagree, stop mutations, establish the current fact, update the stale source, then continue.

## Work model

- One substantial unfinished obligation = one GitHub Issue. Do not create issues for every implementation step.
- One primary NOW item at a time; independent production-critical bugs may interrupt it.
- A code change uses an isolated branch/PR when appropriate. PR/commits/CI preserve implementation history and must not be rewritten merely to make history look cleaner.
- Status follows evidence: open issue = unfinished; branch/PR = in progress; ready PR + applicable green checks = reviewable; merged = implemented; deployed marker + smoke/acceptance = production done when production is required.
- Merge/push never implies deploy.
- Close production-relevant issues only after required production evidence exists.
- Keep historical Linear references in migrated issue/PR descriptions when they materially explain origin or intent.

## Safety invariants

- Never commit real resident data, backups, secrets, tokens or production credentials.
- Do not invent or silently discard ambiguous owner, account, plot, meter, reading or import data. Preserve it for review.
- Do not bypass application validation/history with direct SQL or `QuerySet.update()` / `bulk_update()` for audited application changes.
- Do not weaken authentication, MFA, permissions, CSRF or history just to make a test pass.
- Schema changes and production deployment require an explicit compatibility/rollback path and a verified backup where relevant.
- Test/sandbox verification comes before production for substantial changes.
- Production is not a debugging environment.
- A user-reported UI regression must be reproducible or explicitly smoke-tested before adding more UI work. Controller and resident critical flows remain regression-sensitive.

## Workflow

1. Restore: active Issue/PR → integration HEAD → applicable CI → production marker.
2. Diagnose the smallest relevant code/docs/data surface.
3. Make the smallest safe change; avoid unrelated cleanup.
4. Run targeted validation during iteration and full applicable gate before merge.
5. Review diff and preserve traceability from Issue → PR → merge SHA.
6. For production work: precheck → backup/rollback readiness → controlled deploy → marker/service/HTTP/user-flow smoke.
   Use the reviewed fixed-action procedure in `ops/DEPLOYMENT.md`; tool bootstrap,
   release approval and actual deployment are separate evidence. Pin the exact
   candidate and helper checksum, preserve request/result and recovery paths,
   and update the procedure/checkpoint when this contract changes.
7. Close only when the Issue acceptance criteria and applicable Definition of Done are evidenced.
8. Update `docs/PLAN.md` only when roadmap/invariants change; update Notion checkpoint only when recovery state changes.

For truly parallel independent work use one issue/executor/branch/worktree/PR per task. Do not create a worktree when there is no parallelism benefit.

## Validation

Run the smallest relevant subset during iteration, then the full applicable gate before PR/merge. From the repository root, in the configured Python environment:

```bash
python backend/manage.py check
python backend/manage.py makemigrations --check --dry-run
python backend/manage.py test water config
python -m unittest discover -s ops/tests -v
bash -n ops/deploy-compatible.sh ops/deploy-water-workspace.sh ops/backup-trud-site.sh
```

For UI work, verify the critical role-specific flow and a mobile viewport. Use targeted Playwright E2E and inspect trace/artifacts on failure.

## Documentation map

- `README.md` — repository entry point.
- `backend/README.md` — Django/data/runtime rules.
- `docs/PLAN.md` — roadmap and permanent project rules, not a duplicate task tracker.
- `docs/STAFF-WORKSPACE-ARCHITECTURE.md` — approved Staff Workspace product architecture.
- `docs/ROLES.md` — roles and permissions.
- `docs/MFA.md` — MFA behavior.
- `ops/DEPLOYMENT.md` — deploy/rollback procedure.
- `ops/RECOVERY.md` — recovery procedure.
