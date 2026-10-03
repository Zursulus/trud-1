# Isolated Orchestra chat-v2 queue contract

This is the operating contract for a non-production durable controller candidate.
It defines required behavior; it does not claim that acceptance has passed. A
passing transport smoke test alone does not prove autonomous publication.

## Isolation and ownership

- Legacy eligibility uses `agent:ready` and its outbox is
  `/home/chatgpt-remote/orchestra-local-v1/outbox`.
- chat-v2 eligibility requires owner-authored issues marked
  `ORCHESTRA_QUEUE: chat-v2`, without `agent:ready`. Its state and outbox live
  under `/home/chatgpt-remote/orchestra-chat-v2`. Eligibility and outboxes must
  remain disjoint; neither queue may consume the other's work.
- Queue selection and publication state must be durable outside the Codex
  thread. Exactly one writer may act under both a durable lease and an actual
  filesystem lock. A fencing token must bind the current owner to each action;
  stale owners must be rejected.

## Dispatch and publication

Before execution, durably capture dispatch intent, including the issue, owner,
fence, base branch and SHA, allowed paths, verification profile, and intended
action. For this acceptance task these are:

- Base: `release/cycle3-final-rc` at
  `27f62efcb8381e34f3de895c9fc41d1613c9cfd0`.
- Allowed path: `docs/orchestra-queue-safety.md` only.
- Verification profile: `docs`.

Before Draft PR publication, the controller must independently verify the exact
Git parent, tree, and file bytes against the captured intent and patch evidence.
Executor reports or transport success cannot substitute for that verification.
The trusted controller owns publication, exact evidence archival, and its one
transaction comment; the executor returns an uncommitted patch.

Unknown executor or connector results must hold the queue for reconciliation.
Record the uncertain action durably and reconcile its actual outcome before
retrying or advancing; uncertainty must never be treated as success or failure.

## Advancement and separate gates

`NEXT` is permitted only when all conditions hold: a terminal publication
journal exists, its archive and fence match, the owner is released, and no action
is pending. Completion of execution alone cannot advance the queue.

Merge, deployment, and production changes remain separate gates with their own
authorization and verification. This queue contract does not authorize them.
