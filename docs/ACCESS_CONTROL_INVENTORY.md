# Access Control Inventory — current production to V2 mapping

Baseline: production `faa86c96d44404b9033c58513e5915828a30ffed`.  
Purpose: enumerate existing business actions and map them to semantic V2 capabilities/scopes before implementation.

## 1. Current authorization mechanisms

Current production combines:

1. `is_superuser` — technical bypass and some exceptional corrections.
2. `is_staff` — gate to Django admin and `/work/` because work routes use `admin.site.admin_view`.
3. Django `Group` / `Permission` — global service rights.
4. `ResidentIdentity` — verified User ↔ Person, currently rejects staff.
5. `PortalGrant` — granular Person → Account portal capabilities.
6. legacy `ResidentAccess` — broad User → Account portal access.
7. `ControllerLineAccess` — User → WaterGroup, currently mixes line senior/controller semantics.
8. `BoardMembership` — User-based board position and board-poll eligibility.
9. `TsnMembership`, `PlotRelation`, `Membership` — business facts used by workflows, not generic permission grants.

Production code contains at least 61 explicit `water.*` / `public_site.*` permission literals in addition to ordinary Django model permissions.

## 2. Current stock Groups

### Администратор ТСН
Broad water, resident-access, finance, governance, public-content and audit capabilities. Still not equivalent to superuser.

### Оператор воды
Water model view permissions + official reading/group-consumption creation.

### Контролёр воды
`view/add_controllerreadingsubmission` + `use_controller_workspace`. The scoped workspace currently behaves partly like a line-senior workspace.

### Закрытый реестр членов ТСН
Private Person/PlotRelation/import/member-registry access plus access-request processing.

There are no first-class stock Groups for finance-only, appeals-only, documents/publication-only, governance-only, access-admin-only or security-only work, even though the application already supports those distinct operations.

## 3. Workspace action matrix

Legend: `all` means organization-wide unless later restricted by assignment scope.

### Home / search / accounts

| Existing action | Current check | Target capability | Target scope |
|---|---|---|---|
| Open/search account list | `view_account` or controller workspace | `accounts.view` | all / water_group |
| View land-plot details | `view_landplot` | `plots.view` | all / account / land_plot |
| View current water membership of account | `view_membership` or controller group filter | `water.topology.view` | all / water_group / account |
| View meter data on account | `view_meter` or controller group filter | `water.meters.view` | all / water_group / account |
| Export account cards | `export_account` | `accounts.export` | all / selected accounts |
| View finance summary inside account | `view_charge` + `view_payment` | `finance.view` | all / account |
| View appeals count inside account | `view_residentappeal` | `appeals.view` | all / account |
| View document count inside account | `view_accountdocument` | `documents.account.view` | all / account |

### Water

| Existing action | Current check | Target capability | Target scope |
|---|---|---|---|
| View water dashboard/meters | `view_meter` or `use_controller_workspace` | `water.view` | all / node / water_group / account |
| Enter official Reading | `add_reading` | `water.reading.submit_official` | all / node / water_group |
| Submit controller observation | `add_controllerreadingsubmission` | `water.observation.submit` | all / node / water_group |
| Senior review of resident observation | `use_controller_workspace` + active ControllerLineAccess | `water.observation.review_line` | water_group |
| Final approve/reject submission | `change_controllerreadingsubmission` | `water.observation.finalize` | all / node |
| View reading journal/review | `view_reading` | `water.reading.view` | all / node / water_group / account |
| View balance/loss data | `view_reading` + `view_meter` + `view_watergroup` | `water.balance.view` | all / node / water_group |
| Export readings | `export_reading` | `water.export` | all / node / water_group |
| Reassign historical Reading to another meter | superuser only | `water.history.correct` | all, exceptional |
| Manage nodes/groups/memberships/meters | Django model add/change rights | `water.topology.manage` | all / node |
| Manage group consumption | model rights | `water.group_consumption.manage` | all / water_group |

Important current problem: `ControllerLineAccess` is labelled “Старший / контролёр”, permits one overlapping assignment per group and powers both line-scoped meter work and resident-submission review. V2 must split senior/deputy/controller.

### Finance

Entry to finance workspace currently requires all of `view_account`, `view_billingperiod`, `view_charge`, `view_payment`, `view_paymentallocation`.

| Existing action | Current check | Target capability | Scope |
|---|---|---|---|
| View periods/charges/payments/account finance | finance view bundle | `finance.view` | all / account |
| Calculate billing period | `change_billingperiod` + `add_charge` + `change_charge` | `finance.period.calculate` | all |
| Approve draft charge | `change_charge` | `finance.charge.approve` | all / account |
| Cancel draft charge | `change_charge` | `finance.charge.cancel` | all / account |
| Approve period | `change_billingperiod` | `finance.period.approve` | all |
| Close period | `change_billingperiod` | `finance.period.close` | all |
| Create payment | `add_payment` | `finance.payment.create` | all / account |
| Confirm pending payment | `change_payment` | `finance.payment.confirm` | all / account |
| Reverse confirmed payment | `change_payment` | `finance.payment.reverse` | all / account |
| Allocate payment | `add_paymentallocation` | `finance.payment.allocate` | all / account |
| Manage tariff/policy/assignments | model add/change rights | `finance.policy.manage` | all |
| Export statement | finance account view path; logged | `finance.export` | all / account |

V2 keeps these operations separate so maker/checker can be enabled later.

### Appeals

| Existing action | Current check | Target capability | Scope |
|---|---|---|---|
| List/read resident appeals | `view_residentappeal` | `appeals.view` | all / account |
| Reply to resident | `change_residentappeal` | `appeals.reply` | all / account |
| Change workflow status | `change_residentappeal` | `appeals.status.change` | all / account |
| Close resolved appeal | `change_residentappeal` | `appeals.close` | all / account |
| View/download attachments | `view_residentappeal` | `appeals.attachment.view` | all / account |
| Manage staff-side attachments | `change_residentappeal` | `appeals.attachment.manage` | all / account |

### Documents / public content

| Existing action | Current check | Target capability | Scope |
|---|---|---|---|
| View account documents | `view_accountdocument` | `documents.account.view` | all / account |
| Create account document | `add_accountdocument` | `documents.account.create` | all / account |
| Edit account document metadata | `change_accountdocument` | `documents.account.edit_metadata` | all / account |
| Download account document | account-document view permission | `documents.account.download` | all / account |
| View public documents | `public_site.view_publicdocument` | `documents.public.view` | all |
| Create public document | `public_site.add_publicdocument` | `documents.public.create` | all |
| Edit/publish public document | `public_site.change_publicdocument` + publication form rule | `documents.public.edit` / `documents.public.publish` | all |
| View news | `public_site.view_publicnews` | `news.view` | all |
| Create news | `public_site.add_publicnews` | `news.create` | all |
| Edit/publish news | `public_site.change_publicnews` + publication confirmation | `news.edit` / `news.publish` | all |

Current forms already protect immutable originals and require publication confirmation. V2 must preserve those invariants and optionally separate editor/publisher.

### Resident access / identities

| Existing action | Current check | Target capability | Scope |
|---|---|---|---|
| View access workspace | bundles of resident-access model views/private registry | `access.view` | all / account |
| Review access request with PII | `access_private_registry` + `view_residentaccessrequest` | `access.request.review` | all |
| Approve/reject request | private registry + `change_residentaccessrequest` (+ invite right to approve) | `access.request.decide` | all |
| Create Person from access workflow | private review + `add_person` | `access.person.create` | all |
| Issue granular invite | private review + `add_residentinvite` | `access.invite.issue` | account/person |
| View PortalGrant | `view_portalgrant` + access bundle | `access.grant.view` | all / account / person |
| End PortalGrant | `change_portalgrant` | `access.grant.end` | account/person |
| End legacy ResidentAccess | `change_residentaccess` | `access.grant.end` | account/person |
| Issue password-reset link | `add_residentpasswordreset` | `access.password_reset.issue` | person/user |
| Revoke invite | `change_residentinvite` | `access.invite.revoke` | person/account |
| Revoke reset link | `change_residentpasswordreset` | `access.password_reset.revoke` | person/user |
| Manage future V2 roles/scopes | not implemented | `access.assignment.manage` | all / person |
| Review delegation | not implemented | `access.delegation.review` | all / person/account |
| Explain effective access | not implemented | `access.audit.view` / `access.explain` | person |

### Private registry

| Existing action | Current check | Target capability | Scope |
|---|---|---|---|
| Open private registry | `access_private_registry` | `registry.view` | all |
| View contacts / Person data | same boundary + model views | `registry.contacts.view` | all / person |
| Create/change Person/PlotRelation/member-registry/import rows | private model add/change | `registry.edit` | all / person/plot |
| Export private data | restricted admin/export flow | `registry.export` | all |

Operational line-senior/controller roles must not receive private phone/email automatically.

### Governance / board

| Existing action | Current check/eligibility | Target capability/entitlement | Scope |
|---|---|---|---|
| Staff view of governance workspace | `view_boardpoll` | `governance.board.view` | all |
| Create poll/questions | `add_boardpoll` + `add_boardquestion` | `governance.poll.create` | board |
| Edit open poll | `change_boardpoll` | `governance.poll.edit` | board/event |
| Close poll | `change_boardpoll` | `governance.poll.close` | board/event |
| Add immutable protocol | `add_boardprotocol` | `governance.protocol.add` | event |
| Member view/vote/comment | active BoardMembership | board electorate entitlement | event/person |
| Board audit | model/workspace view | `governance.audit.view` | event |
| Manage board membership | Django model permissions | `governance.membership.manage` | all/person |
| General resident / TSN-member voting | postponed; not implemented in #99 | none | out of scope |

Current `BoardMembership`/`BoardVote` workflow is retained. V2 adds a Person link to BoardMembership so board status belongs to the real person while existing votes remain compatible with the authenticated User.

### Security

Integration branch contains `SecurityAlert` workspace not yet part of inspected production release:

- view alerts -> `security.alert.view`;
- mark/review alert -> `security.alert.review`.

This must be included in V2 even though it is integration-only today.

### Import / export / exceptional administration

| Existing action | Current check | Target capability | Scope |
|---|---|---|---|
| Stage Excel import | `add_importbatch` | `system.import.stage` | all |
| Apply/change staged import | `change_importbatch` | `system.import.apply` | all |
| Export accounts | `export_account` | `accounts.export` | all |
| Export readings | `export_reading` | `water.export` | all / scoped future |
| Export finance statement | finance account view flow | `finance.export` | account/all |
| Reassign historical reading | superuser | `water.history.correct` | exceptional all |
| Manage User records | superuser-only StaffAdmin | `system.user.manage` | all |
| Manage/reset MFA | technical flows | `system.mfa.manage` | all |
| Django admin technical access | `is_staff`/superuser | `system.django_admin` | all |

## 4. Resident portal matrix

Current granular PortalGrant fields map as follows:

| PortalGrant field | V2 semantic capability | Scope |
|---|---|---|
| `can_view_account` | `resident.account.view` | account |
| `can_view_finance` | `resident.finance.view` | account |
| `can_submit_water` | `resident.water.submit` | account |
| `can_view_documents` | `resident.documents.view` | account |
| `can_use_appeals` | `resident.appeals.use` | account |
| `can_represent` | `resident.represent` | account, policy-gated |

Legacy ResidentAccess currently resolves to broad access (account + finance + water + documents + appeals; representation only for owner/representative roles). V2 compatibility adapter must preserve this exactly until each legacy grant is explicitly migrated.

## 5. Business relationship matrix

| Existing entity | Meaning | V2 treatment |
|---|---|---|
| Person | real human | central principal |
| User | login/authentication | linked identity, not business principal |
| LandPlot | physical/legal plot | scope/business object |
| Account | settlement/work account | account scope |
| PlotRelation | owner/representative fact | business fact; possible basis for explicit grants/delegation, never broad implicit permission |
| TsnMembership | TSN membership | business fact + possible voting eligibility |
| ResidentIdentity | verified User ↔ Person | generalize to multi-capacity Person identity |
| PortalGrant | granular resident Account authority | V2 adapter then account assignment |
| ResidentAccess | legacy broad resident Account authority | V2 adapter, later migrate |
| WaterGroup | water line/group | scope |
| Membership | Account belongs to WaterGroup by date | topology fact; dynamically determines houses under line senior |
| ControllerLineAccess | current scoped senior/controller assignment | replace with distinct senior/deputy/controller V2 assignments |
| BoardMembership | chair/member position | migrate to Person-centric position/eligibility |
| ChargeObligation | explicit finance subject/basis | business fact, not generic access |
| MemberRegistryEntry | private registry index/contact link | protected data, not access source |

## 6. Current structural gaps confirmed by inventory

1. Resident and staff/service capacities are mutually exclusive in current identity/portal guards.
2. Line senior and controller are technically conflated.
3. Many modules have granular actions but no usable role preset.
4. Scope is implemented ad hoc only for resident Account grants and controller WaterGroup access.
5. Global Django permissions cannot express “same action, only this line/account/node”.
6. Permission logic is repeated across views/templates/services.
7. Board voting identifies voter by User, making business identity dependent on login record.
8. There is no delegation model despite `can_represent` existing.
9. There is no central effective-access explanation/preview.
10. Sensitive maker/checker separations are not modeled even though operations are already separable.

## 7. Inventory completion gate

Before V2 implementation replaces an existing module, each route/service/admin action must be registered with:

- semantic capability;
- allowed scope types;
- delegable yes/no;
- sensitivity/MFA level;
- whether maker/checker policy applies;
- legacy compatibility source;
- regression tests for allow + deny + wrong-scope cases.

No current permission check is removed until parity tests demonstrate equivalent or stricter behavior through the V2 resolver.
