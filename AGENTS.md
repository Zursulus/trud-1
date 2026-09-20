# AGENTS.md — Труд-1

## Purpose

Production-backed site and Django workspace for ТСН «ТРУД-1». Prefer the smallest reversible change that satisfies the active task. The public site, staff admin and resident cabinet are separate surfaces and must not be assumed to share the same deployment behavior.

## Sources of truth

Use this order before substantial work:

1. Active Linear issue — current task, scope, NOW/NEXT and Definition of Done.
2. `docs/PLAN.md` — current repository plan and project checkpoints.
3. `backend/README.md` — data-model, runtime and safety invariants.
4. `ops/DEPLOYMENT.md` and `ops/RECOVERY.md` when deployment/recovery is in scope.
5. Production state only when the issue requires live verification.

If sources disagree, stop mutations, establish the current fact, update the stale source, then continue.

## Safety invariants

- Never commit real resident data, backups, secrets, tokens or production credentials.
- Do not invent or silently discard ambiguous owner, account, plot, meter, reading or import data. Preserve it for review.
- Do not bypass application validation/history with direct SQL or `QuerySet.update()` / `bulk_update()` for audited application changes.
- Do not weaken authentication, MFA, permissions, CSRF or history just to make a test pass.
- Schema changes and production deployment require an explicit compatibility/rollback path and a verified backup where relevant.
- Test/sandbox verification comes before production for substantial changes.
- A user-reported UI regression must be reproducible or explicitly smoke-tested before adding more UI work. The controller menu and controller-reading flow are critical.

## Workflow

- Read the active Linear issue before choosing work.
- Use minimum viable context: inspect only the code/docs relevant to that issue.
- For truly parallel independent work, use one issue/executor/branch/worktree/PR per task. Do not create a worktree when there is no parallelism benefit.
- Fetch/reconcile the target branch before starting an isolated worktree.
- Make the smallest safe patch; avoid unrelated cleanup.
- Review the diff and run the relevant validation before commit/PR.
- After substantive work, update Linear and any project-state documentation whose conclusions changed.

## Validation

Run the smallest relevant subset during iteration, then the full applicable gate before PR/merge. From the repository root, in the configured Python environment:

```bash
python backend/manage.py check
python backend/manage.py makemigrations --check --dry-run
python backend/manage.py test water config
python -m unittest discover -s ops/tests -v
bash -n ops/deploy-compatible.sh ops/deploy-water-workspace.sh ops/backup-trud-site.sh
```

For UI work, verify the critical staff/controller flow and a mobile viewport. Once Playwright coverage exists, run the targeted E2E suite and inspect its trace/artifacts on failure.

## Documentation map

- `README.md` — repository entry point.
- `backend/README.md` — Django/data/runtime rules.
- `docs/PLAN.md` — current project plan.
- `docs/ROLES.md` — roles and permissions.
- `docs/MFA.md` — MFA behavior.
- `ops/DEPLOYMENT.md` — deploy/rollback procedure.
- `ops/RECOVERY.md` — recovery procedure.
