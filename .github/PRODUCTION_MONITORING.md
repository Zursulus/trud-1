# Production monitoring / Orchestra

The monitoring lane observes; it never selects a task, publishes a patch, deploys,
restarts a service or turns a paused project into an active one.

Authority: current WORK SYSTEM → PROJECT CACHE INDEX → project PROJECT STATE →
GitHub Issue/PR and release evidence. Orchestra's sandbox SQLite is runtime state;
the web dashboard is a rebuildable projection. Application production is separate
from the Orchestra runtime and public control-plane ingress.

## GitHub Production status

This workflow runs from the default branch `main`, independently of Orchestra.
PRs run only offline regression; they never probe production.

- `MARKER_STATUS=OK`: the deployment endpoint responded and project, exact SHA
  and timezone-aware deployment timestamp are valid. This is not full business
  flow acceptance.
- `MARKER_STATUS=UNKNOWN`: the checking channel could not read the endpoint.
  The check fails; site availability is unknown.
- `MARKER_STATUS=INVALID`: the response did not satisfy the deployment contract.
- `RELEASE_STATUS=MATCH/MISMATCH`: compare only with an explicitly configured
  approved production release. Mismatch fails and requires reconciliation.
- `RELEASE_STATUS=UNKNOWN`: no approved baseline was supplied. Marker observation
  can pass; release acceptance is not claimed.
- `RELEASE_STATUS=INVALID_BASELINE`: the configured baseline is not an exact
  lowercase 40-character SHA. The configuration check fails.

The approved baseline can be supplied as manual-dispatch input
`expected_production_commit`, or as repository variable
`TRUD_EXPECTED_PRODUCTION_COMMIT`. Only mirror a reviewed canonical release
decision/receipt; never fill it from integration HEAD or the observed marker
merely to make the comparison green. No baseline is silently inferred.

Integration/candidate comparison belongs to the project observer and is
informational until the project canon requires a specific accepted production
release. The old `production == feature/water-admin` gate is removed.

## Notifications and freshness

Report source, exact ref/version, observed-at time and expected authority.
Deduplicate by material state: project, observation, release target, failure
classification and gate. A new scheduled run ID alone is not a new incident.
Known PAUSED, expected external gates and terminal synthetic blocked tasks are
not runtime failures. Runtime success is not product DONE.

Current canonical sources:

- [WORK SYSTEM](https://app.notion.com/p/3e0fadeec989813e93a4dad93e747ea0)
- [PROJECT CACHE INDEX](https://app.notion.com/p/3e8fadeec98981518a75cf297b7561f5)
- [Труд-1 PROJECT STATE](https://app.notion.com/p/3e8fadeec98981ce8559d6a92d9fa556)
- [Orchestra PROJECT STATE](https://app.notion.com/p/3e8fadeec9898169ba53c856cc0b5d4c)
- [Orchestra status](https://app.notion.com/p/3f1fadeec9898175bab4fe81850335ad)
