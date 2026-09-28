# Access Control V2 — person-centric authority model

Status: architecture specification for Issue #99.  
Baseline inspected: production `faa86c96d44404b9033c58513e5915828a30ffed` plus comparison with `feature/water-admin`.

## 1. Goal

Build one coherent authority system in which one real person can simultaneously be:

- an ordinary resident with one or more personal accounts/plots;
- a TSN member;
- an owner or representative of one or more plots;
- senior of one or more water lines;
- controller of selected lines/nodes/meters;
- board member or chair;
- holder of a finance/documents/appeals/registry/service role;
- a delegate for another person for an explicitly bounded subset of powers.

The system must not force separate logins for these capacities. One person uses one login and receives several independent contexts. Permissions are composable, date-bounded, scoped, auditable, explainable and deny-by-default.

## 2. Core rule: Person is the business principal

`Person` is the stable identity of a human. `User` is only an authentication/login record.

Target relation:

`User 1:1 Person` for ordinary human accounts, without the current prohibition on `is_staff`.

A change of email, username, password or MFA must not change ownership, TSN membership, board membership, line position, delegations or historical authority.

Technical emergency/system accounts may remain person-less, but they are outside ordinary TSN business roles.

### Required migration

Current `ResidentIdentity` is resident-only and rejects staff users. It must evolve into a general verified identity link (working name `PersonIdentity`) or be relaxed/renamed so a resident can also hold service roles.

`is_staff` must stop meaning “not a resident”. It should be reserved for Django-admin/technical access. `/work/` must eventually use its own capability gate instead of `admin.site.admin_view` as the business authorization boundary.

## 3. Separate facts, entitlements and permissions

Three layers must never be conflated.

### 3.1 Business facts

These describe reality and legal/organizational status:

- `PlotRelation`: Person ↔ LandPlot, owner or representative, date-bounded;
- `TsnMembership`: Person is a TSN member, date-bounded;
- `BoardMembership`: person is chair/board member, date-bounded;
- `Membership`: Account ↔ WaterGroup, date-bounded;
- Account ↔ LandPlot;
- financial `ChargeObligation` subject;
- resident number/private registry facts.

Facts do not automatically grant generic application permissions.

### 3.2 Workflow entitlements

Some facts legitimately define eligibility for a specific workflow, not broad system access. Examples:

- active BoardMembership can make a person eligible for a board ballot;
- active TsnMembership can make a person eligible for a TSN-member ballot when that voting event uses that electorate;
- WaterGroup membership determines which accounts belong to a senior's line.

Eligibility must be explicit in the workflow policy and, for voting, snapshotted at the correct date.

### 3.3 Operational authorization

Operational work is granted explicitly through assignments/capabilities/scopes, for example:

- line senior;
- controller;
- water operator/moderator;
- finance viewer/cashier/accountant;
- appeals operator;
- documents/publication operator;
- registry operator;
- access administrator;
- governance secretary;
- security reviewer;
- business administrator.

## 4. Target authorization objects

### 4.1 AccessAssignment

A date-bounded operational assignment to a Person.

Fields/concepts:

- `person`;
- `role_code` and `role_version`;
- scope (see below);
- `starts`, `ends`;
- `basis` / decision/document reference;
- `granted_by`;
- `granted_at`;
- status: scheduled / active / ended / revoked;
- notes;
- immutable history.

Assignments are additive. There is no implicit right based on name/email/phone/ownership matching.

### 4.2 AssignmentCapability

Each assignment resolves to a snapshot of atomic capabilities. This prevents a later role-template edit from silently expanding already-issued authority.

Fields:

- assignment;
- capability code;
- origin: role-default / manual-added;
- optional note.

Removing a role-default capability before issue simply excludes it from the issued snapshot. Existing assignments are never silently rewritten when templates change.

### 4.3 AccessScope

Use explicit FK-backed scopes rather than a free-form generic object pointer.

Supported scope types:

- `self` — the person’s own identity/context;
- `account` — one Account;
- `land_plot` — one LandPlot where a plot-specific action is needed;
- `water_group` — one line/group;
- `supply_node` — one common water node;
- `all` — organization-wide;
- `person` — only for narrowly defined private-registry/access-management cases.

A scope record has exactly the fields appropriate to its type. Scope compatibility is validated against the capability and role template.

### 4.4 AccessRestriction

Rare, explicit high-priority suspension for a Person/User/capability/scope, with dates, reason and actor. Use it for security/disciplinary/emergency suspension, not as a normal way to construct roles.

Normal permissions remain additive; restrictions have higher precedence.

### 4.5 Delegation

A delegation is not the same as a staff role. It transfers a bounded subset of powers from one Person to another.

Fields/concepts:

- delegator Person;
- delegate Person;
- source authority (the concrete account grant/assignment/fact that permits delegation);
- scope;
- delegated capability subset;
- starts/ends;
- basis/document reference;
- created_by / verified_by;
- accepted_at when required;
- revoked_at / revoked_by / reason;
- `allow_redelegation=False` by default;
- immutable audit.

Rules:

1. A person cannot delegate more than they effectively hold.
2. Delegation cannot outlive or exceed the source authority.
3. Capability must be marked delegable by policy.
4. Delegated scope cannot be broader than source scope.
5. Re-delegation is forbidden by default.
6. Revoking/ending the source immediately makes the delegation ineffective without deleting history.
7. Sensitive/legal delegations can require staff verification before activation.

Generic delegation must never transfer system administration, registry administration, security administration, final moderation or other capabilities marked non-delegable.

## 5. Existing resident access and future personal powers

Current PortalGrant already provides six account-scoped capabilities:

- account/basic view;
- finance view;
- water submission;
- account documents view;
- own appeals;
- representation.

These become the first compatibility adapter to V2. PortalGrant remains authoritative during migration, then may be migrated to account-scoped AccessAssignments.

Resident UI must support per-account toggles rather than an all-or-nothing “resident role”.

A person can have multiple accounts with different powers on each account.

### Representation/delegation

`can_represent` should not be treated as a magic grant of every future action. It means the holder may perform only actions whose capability policy explicitly allows representative use. A future delegation screen must show exactly what can be passed on.

Examples of potentially delegable account powers:

- view basic account data;
- submit water readings;
- view account documents;
- use appeals;
- view finance where policy permits;
- defined representative actions.

Whether a specific legal action or vote is delegable is a separate policy decision, not inferred from `can_represent`.

## 6. Water topology and line accountability

The existing topology is correct and should remain the source of scope:

`SupplyNode -> WaterGroup -> Membership -> Account -> LandPlot`

A line senior is assigned to a WaterGroup. The houses/accounts under that senior are not copied into the assignment: they are resolved from active `Membership` rows for the requested date.

Benefits:

- moving an account to another line automatically changes accountability from the effective date;
- history remains reconstructable;
- no duplicated “list of houses for senior” can drift.

### Line senior

Separate role from controller.

Default capabilities on `water_group` scope:

- view line summary/topology needed for operations;
- view line meters and individual meters of active member accounts;
- submit line/assigned readings;
- see resident submissions for accounts in the line;
- confirm/flag resident observations before final moderation;
- view line balance/loss information appropriate to the role.

Private contact data is not included automatically. Registry/contact visibility requires a separate capability.

Support positions:

- one primary senior per line and period;
- optional deputies/temporary substitutes with their own dated assignments.

### Controller

Independent from line senior. Multiple controllers may overlap on a line/node if policy allows.

Controller capabilities are observation/control-oriented, for example:

- view assigned meters/topology;
- submit independent observations;
- optionally compare with reported values;
- no automatic right to perform line-senior review;
- no automatic resident finance/documents/private-registry access.

Scopes may be water_group, supply_node or selected future meter set if required.

### Water operator / water moderator

Keep separate capabilities for:

- view;
- official reading entry;
- observation capture;
- final moderation/approval;
- topology management;
- export;
- exceptional correction/reassignment.

Exceptional historical correction remains highly privileged and separately audited.

## 7. Voting and governance

A person can participate in several independent electorates at the same time.

Examples:

- ordinary personal/resident context;
- TSN-member voting context;
- board-member voting context;
- chair context.

They must use the same login/Person identity, not separate accounts.

### 7.1 Board voting

Board eligibility comes from active BoardMembership at the event snapshot date.

Target BoardMembership should be Person-centric. Current User-based membership is migrated/adapted through verified identity.

### 7.2 TSN/general-member voting

This is a distinct electorate. Eligibility is based on the explicit rule of that voting event (normally active TsnMembership, but the system must not hard-code assumptions that belong to the charter/event type).

### 7.3 Voting snapshot

Each voting event freezes its electorate at an explicit snapshot time/date so later membership changes do not rewrite who was entitled to vote at opening.

Target concepts:

- VotingEvent / electorate type;
- EligibilitySnapshot(Person, basis);
- questions/options;
- Vote with `voter_person` (whose legal/organizational vote) and `cast_by_user` (who authenticated);
- capacity: self / authorized proxy where allowed;
- linked delegation/proxy record when used;
- immutable audit.

Uniqueness is on `(question, voter_person)`, preventing double voting if the same person tries through different contexts.

### 7.4 Proxy voting

Do not reuse generic delegation automatically for board/member voting. Voting proxy must be a dedicated policy/record and is disabled unless the relevant governance rules explicitly permit it.

If enabled, record:

- whose vote;
- who cast it;
- exact event/scope;
- legal/organizational basis;
- validity;
- whether the principal already voted;
- full audit.

Board voting and TSN-member voting can have different proxy rules.

## 8. Capability catalog

Names below are the target semantic API; exact implementation names may be adjusted during coding.

### Identity / accounts / plots

- `accounts.view`
- `accounts.edit`
- `accounts.export`
- `plots.view`
- `plots.edit`
- `relations.view`
- `relations.edit`

### Water

- `water.view`
- `water.meters.view`
- `water.reading.submit_official`
- `water.observation.submit`
- `water.observation.review_line`
- `water.observation.finalize`
- `water.balance.view`
- `water.topology.manage`
- `water.export`
- `water.history.correct`

### Finance

- `finance.view`
- `finance.period.calculate`
- `finance.charge.approve`
- `finance.charge.cancel`
- `finance.period.approve`
- `finance.period.close`
- `finance.payment.create`
- `finance.payment.confirm`
- `finance.payment.reverse`
- `finance.payment.allocate`
- `finance.policy.manage`
- `finance.export`

### Appeals

- `appeals.view`
- `appeals.reply`
- `appeals.status.change`
- `appeals.close`
- `appeals.attachment.view`

### Documents / publication

- `documents.account.view`
- `documents.account.create`
- `documents.account.edit_metadata`
- `documents.account.download`
- `documents.public.view`
- `documents.public.create`
- `documents.public.edit`
- `documents.public.publish`
- `news.view`
- `news.create`
- `news.edit`
- `news.publish`

### Resident access / identities

- `access.view`
- `access.identity.verify`
- `access.person.create`
- `access.invite.issue`
- `access.grant.issue`
- `access.grant.end`
- `access.password_reset.issue`
- `access.invite.revoke`
- `access.assignment.manage`
- `access.delegation.review`
- `access.audit.view`

### Private registry

- `registry.view`
- `registry.edit`
- `registry.contacts.view`
- `registry.export`

### Governance

- `governance.board.view`
- `governance.board.vote` (entitlement from snapshot, not ordinary staff role)
- `governance.poll.create`
- `governance.poll.edit`
- `governance.poll.close`
- `governance.protocol.add`
- `governance.audit.view`
- `governance.membership.manage`
- `governance.member_vote` (event entitlement)

### Security / system

- `security.alert.view`
- `security.alert.review`
- `system.import`
- `system.export`
- `system.user.manage`
- `system.mfa.manage`
- `system.django_admin`
- `system.break_glass`

## 9. Role presets

Roles are convenience presets, not the source of truth. A role instance materializes a capability snapshot and a scope.

Proposed presets:

1. Resident/account holder — account-scoped personal functions, configured per account.
2. Account delegate/representative — only explicitly delegated account capabilities.
3. Line senior — one or more WaterGroup scopes.
4. Line deputy — dated substitute with same or reduced line capability set.
5. Controller — water_group/supply_node observation scope.
6. Water operator — organization or node scope.
7. Water moderator — final review/approval; separate from ordinary operator.
8. Finance viewer — read-only.
9. Cashier — create payments, optionally allocation; confirmation separate by policy.
10. Accountant — periods/charges/payments as configured.
11. Appeals operator.
12. Account documents operator.
13. Public content editor/publisher.
14. Private registry operator.
15. Access administrator.
16. Governance secretary/operator.
17. Security reviewer.
18. TSN business administrator — broad business access but not technical superuser.
19. System administrator — technical only; isolated from ordinary business-role design.

`TsnMembership`, `BoardMembership`, owner status and chair status are business facts/positions, not ordinary role presets. They can produce specific workflow entitlements but must not silently unlock unrelated data.

## 10. Composition rules

One Person may hold any number of compatible assignments simultaneously.

Example:

- Portal access to Account “Горная 2” with finance + water + documents;
- Line senior for “Миндальная”;
- Controller for “Виноградная”;
- active BoardMembership;
- finance view-only organization role.

Effective permission is the union of active grants whose scope covers the requested object, minus hard restrictions and invariant failures.

No broadening occurs merely because two unrelated roles coexist.

## 11. Resolver

All business authorization must converge on one service, conceptually:

`access.resolve(user, capability, object/scope, at=...) -> AccessDecision`

Decision contains:

- allowed/denied;
- resolved Person;
- capability;
- requested/effective scope;
- source assignment/grant/delegation/entitlement;
- reason/explanation;
- validity dates;
- restrictions that caused denial.

Convenience API:

- `access.can(...)`;
- `access.require(...)`;
- `access.scoped_queryset(...)`;
- `access.explain(...)`.

Evaluation order:

1. authenticated + active login;
2. technical break-glass/superuser handling with mandatory audit;
3. verified Person identity where required;
4. hard restrictions/suspensions;
5. direct V2 assignments;
6. validated delegations;
7. workflow entitlements (board/member electorate etc.);
8. compatibility adapters (PortalGrant, ResidentAccess, ControllerLineAccess, Django Groups) during migration;
9. otherwise deny.

Templates never become the security boundary. Views/services/API use the same resolver; templates only render the already-resolved capabilities.

## 12. User experience

### 12.1 People directory

A central “People / residents” list, searchable by permitted fields:

- name;
- resident number;
- account/plot;
- line;
- role/position.

Badges can show non-sensitive operational status such as:

- Resident;
- TSN member;
- Owner/representative;
- Line senior / deputy;
- Controller;
- Board member / chair;
- selected service roles.

Private contacts remain behind registry permissions.

### 12.2 Person card

Tabs/blocks:

- identity and login status;
- plots/accounts and relations;
- TSN membership;
- water lines connected through the person’s accounts;
- operational assignments;
- board position;
- personal account grants;
- delegations given/received;
- voting eligibility/history as permitted;
- effective permissions (“why can this person do this?”);
- full audit/history.

Actions:

- add role/position;
- choose scope;
- toggle capabilities before issue;
- schedule start/end;
- suspend/end/revoke;
- create/review delegation;
- preview resulting access before save.

### 12.3 Lines screen

Each WaterGroup shows:

- node;
- primary senior;
- deputies;
- controllers;
- active accounts/houses derived from Membership;
- meters;
- effective dates;
- missing senior / conflicting assignments warnings.

From a house/account it must be possible to navigate back to its current line and responsible senior.

### 12.4 Context switcher

After one login a multi-capacity user can see contexts such as:

- “My account / Мой кабинет”;
- “Line senior — Миндальная”;
- “Controller — Виноградная”;
- “Board”;
- “Work database” sections allowed by service assignments.

This is navigation only; underlying authorization is always resolver-based.

## 13. Security and privacy invariants

- deny by default;
- no rights inferred from email/phone/name/contact fields;
- archived/inactive subjects cannot receive new grants;
- no destructive history rewriting;
- all assignments/delegations have dates and basis;
- all privileged changes record actor and before/after semantics;
- raw invite/reset tokens are never stored;
- sensitive role changes require re-authentication/MFA when implemented;
- private contacts are separate from operational line/account visibility;
- capability/scope compatibility is server-validated;
- no delegation beyond source authority;
- no silent role-template expansion of existing assignments;
- no generic delegation of system/security/final-approval capabilities;
- superuser is technical emergency authority, not a normal business role.

## 14. Separation-of-duties hooks

The capability model must support optional maker/checker policies without redesign:

- payment creator != payment confirmer when policy requires;
- charge calculator != period approver;
- content editor != publisher;
- access request processor != sensitive grant verifier;
- controller observation != final water moderator.

Initially these can be policy flags; capability separation must exist from day one.

## 15. Missing/future pieces to include now in the design

1. Temporary substitution for vacations/absence.
2. Scheduled future roles and automatic expiry.
3. Multiple accounts/plots and joint owners.
4. Multiple controllers per line/node.
5. One primary senior plus deputies.
6. Person death/archive/inactive-login handling without history loss.
7. Transfer/succession of property without copying old authority.
8. Self-service delegation with staff verification for sensitive powers.
9. Notifications to both parties when access/role/delegation changes.
10. Expiry reminders and periodic access review.
11. MFA requirement by sensitive capability/role.
12. Access simulation/preview (“what exactly will Ivanov see/do?”).
13. Access explanation for support/audit.
14. Conflict warnings and orphaned assignments when topology changes.
15. Bulk assignment only with preview and explicit confirmation.
16. Import/export permissions separate from ordinary editing.
17. Emergency break-glass with reason and alert.
18. API uses the same resolver.
19. Machine-readable route/action registry to prevent new endpoints from bypassing capability declarations.
20. Regression test matrix generated from role × scope × action scenarios.

## 16. Migration strategy — no big bang

Phase 0 — inventory/specification
- finish action matrix for every current endpoint/service;
- map each old permission to a semantic capability;
- map object visibility to scopes;
- freeze regression scenarios.

Phase 1 — identity and resolver shell
- generalize ResidentIdentity to Person identity usable by multi-capacity users;
- add resolver and compatibility adapters;
- keep behavior unchanged.

Phase 2 — assignments/scopes
- add AccessAssignment/Scope/Capability/Audit models;
- implement role presets and effective-access explanation;
- add central People / Access & roles UI.

Phase 3 — resident + service coexistence
- decouple `/work/` from `is_staff`/Django admin boundary;
- allow same User/Person to use resident portal and permitted work contexts.

Phase 4 — water split
- migrate `ControllerLineAccess` into distinct line-senior/deputy/controller assignments;
- derive accountable houses from active Membership;
- keep historical behavior through adapter until migration verified.

Phase 5 — delegation
- implement account capability delegation and review workflow;
- no vote proxy by default.

Phase 6 — governance
- make board membership Person-centric;
- introduce electorate snapshots and Person-based vote identity;
- add TSN-member voting as separate electorate when required;
- optional dedicated vote proxy only after explicit governance policy.

Phase 7 — module migration
- finance, appeals, documents, registry, access, governance, security, import/export switch to resolver one module at a time;
- remove duplicated `has_perm` checks only after parity tests.

Phase 8 — legacy retirement
- migrate remaining PortalGrant/ResidentAccess/Django-group business roles where safe;
- preserve immutable historical records;
- keep technical Django permissions only where technically appropriate.

## 17. Acceptance scenarios

The design is not complete unless all scenarios work without separate logins or manual Django-permission surgery:

1. Ordinary resident sees only Account A; finance off, water on.
2. Same resident is line senior for Group X and automatically sees only active accounts in X for line work.
3. Same person is controller for Group Y but cannot perform senior review there.
4. Same person is board member and can vote in board event while retaining normal resident portal access.
5. Same person is also a TSN member and can participate in a separate member electorate without confusing the two ballots.
6. Resident delegates water submission for Account A to another verified person for one month; finance remains private.
7. Delegator’s source authority ends early; delegated access stops immediately and history remains.
8. Account moves from Group X to Group Z; senior X loses operational scope for that account on effective date and senior Z gains it without editing either senior assignment.
9. A deputy senior temporarily covers a line without changing the primary senior’s historical record.
10. Two independent controllers work on one line without becoming seniors.
11. Accountant can view/operate finance but cannot open private registry or water moderation.
12. Cashier can enter a payment but, when maker/checker policy is enabled, cannot confirm their own payment.
13. Content editor can prepare news but cannot publish when publisher separation is enabled.
14. Access administrator can preview exact resulting authority before issuing a grant.
15. Support/admin can explain every effective permission with its source, dates and scope.

## 18. Definition of Done for V2 design

Before implementation is considered designed:

- every current work/portal/admin business action is inventoried;
- every action has one semantic capability;
- every capability declares valid scopes and delegability;
- every role preset is a versioned capability template;
- all current entities are mapped to facts, entitlements, assignments or compatibility adapters;
- line senior/controller are separate;
- resident + service roles coexist on one Person/User;
- board/member voting contexts are distinct;
- delegation semantics and non-delegable rights are explicit;
- migration preserves current production access and history;
- tests cover all acceptance scenarios above.
