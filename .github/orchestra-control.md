# Orchestra control lane

This branch/PR is a non-production control surface for Codex Cloud dispatch from the main Coordinator when direct Cloud thread APIs are unavailable.

Guardrails:
- Never merge this PR; it is an orchestration anchor only.
- Canonical technical work remains in GitHub Issues/PRs/CI.
- Each dispatched task must name its source Issue and bounded RETURN target.
- No production mutation, deploy, merge, credential/access changes, or destructive actions unless a separate explicit gate authorizes them.
- One mutable target has one write owner.
- The Coordinator reads and verifies returned GitHub evidence before dispatching the next bounded task.
