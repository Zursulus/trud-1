# Access Control V2 — relationship map and edge cases

This document complements `ACCESS_CONTROL_V2.md` and `ACCESS_CONTROL_INVENTORY.md`.
It defines what the entities mean and which relationships may or may not create authority.

## 1. Central graph

```text
User (login/auth)
  │ verified identity
  ▼
Person (real human)
  ├── PlotRelation ──> LandPlot ──> Account
  │                                  ├── Membership ──> WaterGroup ──> SupplyNode
  │                                  ├── Meter / Reading
  │                                  ├── Charge / Payment / Documents / Appeals
  │                                  └── personal Account authority
  │
  ├── TsnMembership
  ├── BoardMembership / organizational positions
  ├── AccessAssignment (service/operational role + scope)
  ├── Delegation given / received
  └── VotingRightSnapshot / Vote
```

`Person` is the identity anchor. No other edge is inferred from name, email or phone.

## 2. User and Person

### Target

- One ordinary human Person has at most one active ordinary login User.
- One User identifies one Person after verification.
- The same User may open both personal and operational contexts.
- Service roles do not require a second account.
- Changing email/username/password/MFA does not change Person authority/history.
- Disabling User blocks authentication but keeps every historical Person relationship.
- A Person may exist without a User.
- A delegation/position/ownership record may exist before that person activates a login.
- Technical/system accounts may be person-less and cannot own property, hold TSN membership or vote.

### Identity lifecycle

`unlinked -> pending verification -> verified -> login disabled/replaced`

Identity merge/split is exceptional: never silently merge two People because contacts match. A future reconciliation tool must show evidence and preserve an audit link from superseded records.

## 3. Person, resident, owner, member and payer are different concepts

Do not create a single `resident_role` field.

A Person can independently be:

- a portal user;
- an occupant/contact without ownership;
- an owner of a plot;
- a representative of an owner/plot;
- a payer for an account/obligation;
- a TSN member;
- a non-member owner;
- a line senior/controller;
- a board member/chair;
- an employee/service-role holder.

These statuses can overlap in any valid combination and can start/end on different dates.

## 4. Property and account graph

Current facts already support important non-1:1 cases:

- Person ↔ LandPlot is many-to-many over time through PlotRelation;
- one Person may own/represent multiple plots;
- one plot may have multiple owners/representatives;
- LandPlot optionally points to an Account;
- one Account may currently have multiple LandPlots;
- Account is a settlement/operational object, not proof of ownership.

### Invariants

- ownership never creates login access automatically;
- account access never proves ownership;
- TSN membership never follows automatically from ownership;
- payer identity never proves owner/member status;
- changing property ownership does not rewrite historical PortalGrant/Delegation records;
- new owner/access decisions are explicit and dated.

## 5. Personal account authority

A Person may have different capabilities for each Account.

Example:

- Account A: view + finance + water + documents + appeals;
- Account B: view + water only;
- Account C: delegated view only until a specified date.

The UI therefore shows capability toggles per Account, not only a single “resident” switch.

Possible authority sources during migration:

1. explicit PortalGrant;
2. legacy ResidentAccess adapter;
3. V2 direct personal assignment;
4. validated Delegation.

Sources do not union invisibly when this would bypass restrictions; resolver returns the source used/explanation.

## 6. PlotRelation and representation

`PlotRelation(owner|representative)` is a business/legal fact, not generic application access.

It may be used as verified basis for granting/delegating specific powers, but only through an explicit policy/action.

`resident.represent` means “may perform actions explicitly marked representative-capable”. It does not mean:

- access all private data;
- change ownership;
- create arbitrary delegations;
- vote in every ballot;
- gain staff functions.

## 7. TSN membership

TsnMembership belongs to Person and is date-bounded.

It can affect:

- eligibility for a member electorate when the VotingEvent policy says so;
- charge obligation basis;
- display/status in authorized People views.

It does not automatically grant:

- portal Account access;
- private registry access;
- board membership;
- work-database permissions.

## 8. Water topology

Authoritative operational graph:

`SupplyNode -> WaterGroup -> Membership(Account) -> Account -> LandPlot(s)`

Membership is date-bounded. Therefore the question “which houses are under senior X?” is answered as-of a date:

1. resolve X's active `line_senior` assignment(s);
2. take those WaterGroups;
3. find active Account Memberships for the same date;
4. display Accounts/LandPlots/meters allowed by the senior capability.

Never duplicate the house list in the senior assignment.

## 9. Water positions

### Primary line senior

- one primary senior per WaterGroup/time interval by default;
- line scope only;
- sees operational account/plot label, line membership and meters required to do the job;
- submits line-senior readings/observations to the normal review chain;
- performs line-stage review of resident submissions;
- does not automatically see private contacts or finance.

### Deputy / substitute

- separate dated assignment;
- may be reduced-capability or equivalent to senior;
- does not rewrite/end primary senior merely because a temporary deputy exists;
- UI clearly distinguishes primary vs covering deputy;
- absence/vacation can be scheduled in advance.

### Controller

- independent role;
- can be assigned to WaterGroup or SupplyNode;
- multiple controllers may coexist;
- submits independent control observations;
- does not automatically become senior or perform line-stage approval;
- controller may also separately be senior somewhere else.

### Operator and moderator

- operator creates official readings and operational entries permitted by policy;
- moderator performs final review/approval/rejection;
- exceptional history correction is a different highly privileged capability.

## 10. Who may see the senior/controller

Operational responsibility and private contacts are separate.

Residents in a line may be allowed to see a limited role card such as:

- display name;
- “Старший линии Миндальная”;
- optionally a deliberately published contact channel.

They do not receive access to MemberRegistryEntry/private phone/email merely because that Person is their senior.

Future concept: `RoleContactPublication` / public service contact settings attached to assignment or Person, with explicit publish flags.

## 11. Board and organizational positions

BoardMembership should become Person-centric.

Positions may include at least:

- chair;
- board member;
- future secretary/other charter-defined position where needed.

A position is a business fact/eligibility source, not a blanket admin role.

The same Person may be:

- resident with Account A;
- TSN member;
- board member;
- line senior;
- controller elsewhere.

All are visible as separate badges/contexts after one login.

## 12. Voting model

Do not model “the user votes”. Model “a Person/capacity has a voting right and a User casts it”.

### VotingEvent

Defines:

- event type;
- electorate policy;
- opens/closes/snapshot time;
- questions/options;
- vote weight policy if applicable;
- proxy/delegation policy;
- secrecy/visibility policy;
- quorum/report rules.

### VotingRightSnapshot

Frozen at the event’s defined snapshot:

- voter Person;
- capacity/basis (board membership, TSN membership, other explicit rule);
- weight if the event policy uses weights;
- eligibility evidence/reference.

This prevents later membership/property changes from rewriting historic eligibility.

### Vote

Records:

- voting right / voter Person;
- authenticated `cast_by_user`;
- choice;
- cast/changed timestamp where changes are allowed;
- capacity (`self`, dedicated authorized proxy if permitted);
- proxy record if applicable;
- audit.

One voting right cannot be exercised twice merely because a Person has several UI contexts.

### Separate electorates

Board voting and TSN-member/general-meeting voting are separate contexts. A Person may be eligible for both, but these are not “two votes in one ballot”.

The system must support future event policies rather than hard-code a legal voting rule into access control. In particular, one-person-one-vote vs other weighting/quorum rules belong to the VotingEvent policy and the applicable governing rules.

## 13. Delegation / power transfer

Delegation is Person-to-Person and source-bound.

Example:

Person A has Account A with finance view, water submission and documents. A may delegate only water submission to Person B for 01.07–31.08. B gains no finance or documents.

### Lifecycle

`draft -> pending acceptance/verification -> active -> ended/revoked/expired`

### Recipient without login

A delegation may target a verified Person who has no User yet. It remains pending/recorded until login activation/identity verification satisfies policy. No duplicate Person is created from email alone.

### Delegable categories

Foundation allows personal Account capabilities to be marked delegable. Each capability still has policy metadata.

Operational staff roles are not self-delegable by default. A senior’s vacation cover is a new approved deputy assignment, not an informal generic delegation of the senior role.

### Voting proxy

Separate dedicated record/policy. Generic Account delegation never conveys a vote.

## 14. Financial subject vs access

ChargeObligation can point to Account, Plot, Person or TsnMembership. This is “who/what owes and why”, not “who may see/edit finance”.

A payer may therefore be different from:

- owner;
- member;
- portal user;
- person with finance-view permission.

Finance access remains explicit.

## 15. Appeals/documents and acting capacity

Future records/actions should preserve acting context where material:

- person acting for self/account;
- person acting under delegation;
- staff acting in operational role.

This is especially useful for appeals, uploaded documents and legally meaningful representative actions.

Do not duplicate ordinary audit on every model prematurely; resolver/action audit should provide the authority source and acting capacity.

## 16. Archive, death, succession and termination

- Archiving Person blocks new grants but preserves history.
- Disabling User blocks login but preserves Person facts.
- Ending ownership does not delete past ownership.
- Ending TSN membership does not delete old votes/financial obligations.
- Ending board membership does not alter eligibility snapshots of already-opened/closed events according to event snapshot policy.
- Ending a source authority immediately makes dependent future/effective delegation unavailable while preserving delegation history.
- Property succession/transfer creates new dated facts and explicit new authority; never copy old authority blindly.

## 17. Joint ownership and multiple people per account

System must allow:

- several owners for one LandPlot;
- several People with different Account capabilities;
- owner with no portal login;
- portal user who is payer/representative but not owner;
- one Person related to several Accounts through several plots/grants/delegations.

No “main owner” assumption should become an authorization shortcut. If business UX needs a primary contact, model that as a separate contact designation, not ownership authority.

## 18. Children, guardians and other legal representatives

Do not infer legal capacity from age/contact fields. If the project later needs guardianship or another legal-representative basis, add it as an explicit dated relationship with evidence and a capability/delegation policy. Do not overload PlotRelation or generic `represent`.

## 19. Role visibility and discoverability

Authorized People directory filters should support:

- resident number;
- Person name (only for users allowed to see it);
- Account/plot;
- WaterGroup;
- operational role;
- TSN member status;
- board position;
- active/scheduled/expired assignments.

Line screen should make it trivial to answer both directions:

- “Who is responsible for this line?”
- “Which accounts/houses are currently under this senior?”

Account screen should answer:

- current line + senior/deputy;
- personal authority holders (only for access administrators / appropriate scope);
- water controller assignments relevant to the account where appropriate.

## 20. Access changes and notifications

Every issue/end/revoke/suspension should be auditable. Notification policy should support:

- assignee receives role/access activation/end;
- delegator and delegate receive delegation changes;
- affected line residents may receive senior replacement announcement if configured;
- access administrators receive sensitive assignment/restriction alerts;
- expiry reminders for temporary assignments/delegations.

Notifications must not leak private reason/documents to recipients who lack permission.

## 21. Conflict and integrity checks

Examples to enforce centrally:

- primary senior overlaps on same line are blocked unless explicitly transitioning at a boundary;
- deputies may overlap under policy;
- controllers may overlap;
- assignment cannot target archived Person/scope object;
- invalid scope for capability is rejected;
- delegation subset/scope/time must fit source authority;
- no self-delegation unless a specific policy needs it;
- cyclic delegation rejected;
- re-delegation off by default;
- duplicate effective capability grants may coexist only if harmless, but UI should explain/deduplicate result;
- ending one source does not remove a permission still valid from another independent source;
- restrictions override grants according to explicit precedence;
- identity conflicts prevent activation rather than guessing.

## 22. Periodic review / security hygiene

Needed for a maintainable long-lived system:

- “Who has sensitive access now?” report;
- assignments expiring soon;
- dormant accounts with authority;
- authority held by inactive/archived Persons;
- lines without primary senior;
- accounts not assigned to a WaterGroup;
- conflicting or orphaned scopes after topology changes;
- users with business role but no verified Person;
- delegates whose source authority ended;
- high-risk roles without MFA;
- break-glass events;
- periodic confirmation/recertification by administrator.

## 23. Migration/reconciliation queues

During migration show explicit queues instead of guessing:

- legacy ResidentAccess without PersonIdentity;
- staff User without Person;
- current ControllerLineAccess needing classification as senior vs controller;
- BoardMembership tied to User needing Person link;
- duplicate People suspected by data but not auto-merged;
- old global Django permissions not represented by V2 assignments.

Each queue item must have a deterministic manual resolution action and audit trail.

## 24. UI principle

The administrator edits a **Person card**, not raw permissions.

Normal flow:

1. Find Person.
2. See factual statuses and current scopes.
3. Add a preset (“Старший линии”, “Контролёр”, “Бухгалтер”…).
4. Pick scope and dates.
5. Optionally narrow/extend allowed capabilities within policy.
6. Preview exact effective access.
7. Save with basis.
8. Later end/suspend/revoke without rewriting history.

Advanced raw capability controls exist for access administrators, but are not the default UX.

## 25. Core acceptance example

One Person, one User:

- personal Account “Горная 2”: view + finance + water + documents;
- owner of Plot 47;
- active TSN member;
- primary senior of WaterGroup “Миндальная”;
- controller on SupplyNode “Северный” excluding senior powers there;
- board member;
- temporary delegated water-submission authority for another Account;
- no private-registry access;
- no finance-staff editing;
- can participate in board voting and, when a separate member VotingEvent exists, in that electorate according to its own eligibility snapshot;
- can switch UI contexts after one login;
- every effective action is explainable by one or more dated sources.

If this scenario cannot be represented without another User account or manual Django-permission surgery, V2 is not complete.
