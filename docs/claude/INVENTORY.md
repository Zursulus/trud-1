# Инвентаризация Труд-1 — 15.10.2026

Составил Claude по живому GitHub, Notion и папке на компьютере владельца.
**Справка, не задачи.** ChatGPT/Codex: читать можно, не изменять.

## Главное

1. **`main` не отражает проект.** В нём статический макет (7 коммитов, последний 21.09). README в main устарел.
2. **Production** = коммит `6fb32eff0f389eda944371d802cd14dec8eb2adb` (06.10.2026, «#159 restore workbench contrast»),
   по marker и Notion PROJECT STATE. Он лежит только в 4 ветках:
   `fix/workbench-contrast-regression-20261006`, `fix/release-workbench-contrast-160-20261006`,
   `fix/deploy-broker-v2-133-20261006` (пустой указатель на прод), `fix/deploy-broker-selfservice-133-20261006` (+4, прототип, НЕ деплоить).
3. **Две расходящиеся линии.** «Интеграционная» ветка `feature/water-admin` (в неё влито 91 PR, последний 01.10)
   разошлась с прод-линией 27.09 (`3e489e1`). Прод впереди на ~127 коммитов; у water-admin 121 коммит без
   точного эквивалента в проде (часть, видимо, перенесена переписанными). Нужна сверка по содержимому, прежде чем
   объявлять water-admin устаревшей.
4. **PR цепочкой друг на друга**: #155 → #156 → #157 → #160 → #163 и т.д. — каждый релиз собирался поверх предыдущей
   ветки, а не в main. Все draft, не влиты.
5. **165 веток**, все от `Zursulus`, 118 с последним коммитом в сентябре, 47 — в октябре. 102 целиком входят в прод.
6. **ChatGPT остановлен** владельцем 06.10.2026 (Notion: GLOBAL STOP / PAUSED_BY_USER). С 09.10 в Notion
   есть отдельное разрешение только для проекта Orchestra.

## Состояние на проде (Notion PROJECT STATE, 06.10)

- Сервисы живы: `trud-1-site.service`, Nginx, PostgreSQL 17; `/` = 200, вход в кабинет = 200.
- Счётчики воды: 63 индивидуальных / 61 лицевой счёт. Закрытый реестр: 301 запись, 79 связаны со счетами.
- Жители в кабинете: фактически 1 из 61 счёта может подавать показания. Октябрьских показаний 0.
- Решение ChatGPT: массово права не выдавать; путь — заявка жителя → проверка сотрудником → разовое приглашение.
- Мониторинг GitHub Actions «красный» из-за сравнения прода с water-admin (а не из-за падения сайта); фикс в PR #165 (draft).

## Открытые Issues (10)

| # | Обновлено | Название |
|---|---|---|
| #166 | 2026-10-13 | На будущее: подготовить раздел сайта о расчёте расходов на воду (1 и 2 ОСН) |
| #164 | 2026-10-06 | Production status: align monitoring with Orchestra and approved release |
| #135 | 2026-10-06 | VTB: export current debt registry in real Template 6 format |
| #134 | 2026-10-02 | Future: electronic/hybrid general-meeting voting in resident cabinet |
| #133 | 2026-10-06 | Ops: constrained self-service compatible deploy for Trud-1 |
| #131 | 2026-10-06 | Workbench: direct safe actions for resident access and individual meter binding |
| #80 | 2026-10-06 | Harden appeal composer: safe Office attachments, anti-spam and admin security alerts |
| #76 | 2026-09-28 | Research: obtain primary documents for creation and land allocation of Труд-1 |
| #75 | 2026-09-28 | Future legal input: obtain and verify registered TSN charter |
| #74 | 2026-10-06 | Blocked: prepare VTB bank exchange from real sample |

Ключевые блокеры по Notion:
- **#133** deploy без ручного `su -` — НЕ деплоить ни одну из веток-прототипов.
- **#135 / #74** ВТБ — нет подтверждённых банковских констант (счёт, раздел приёма, код услуги). Не угадывать.
- **#80 / #131** — код в проде, но приёмка с реальным входом не проведена.
- Утечка: PR #158 с данными из прода закрыт; старые объекты в GitHub надо чистить через GitHub Support.

## Ветки по категориям

Колонка «+к проду» — сколько коммитов ветки нет в production-коммите.


### B. Есть открытый PR — живая незавершённая работа — 15

| Ветка | Посл. коммит | +к проду | PR | Последнее сообщение |
|---|---|---|---|---|
| `fix/production-monitor-orchestra-164-20261006` | 2026-10-06 | 3 | #165 open→main | fix: align production monitoring with Orchestra release contract |
| `fix/release-public-atomic-20261005` | 2026-10-06 | 4 | #157 open→fix/ui-flow-polish-20261005 | Add CI guard for synthetic maintenance fixtures |
| `fix/release-workbench-160-20261006` | 2026-10-06 | 8 | #162 open→fix/deploy-broker-bootstrap-20261005 | #161 test #160 release launcher contract |
| `fix/release-workbench-contrast-160-20261006` | 2026-10-06 | 4 | #163 open→fix/workbench-contrast-regression-20261006 | Test corrected #160 release postcheck |
| `agent/144-appserver-e2e-smoke` | 2026-10-03 | 3 | #147 open→main | agent: issue #144 |
| `agent/144-orchestra-e2e-smoke` | 2026-10-03 | 3 | #145 open→main | agent: issue #144 |
| `agent/146-orchestra-cron-smoke` | 2026-10-03 | 1 | #148 open→release/cycle3-final-rc | agent: issue #146 |
| `agent/151-151-1791047086-0c8690a8` | 2026-10-03 | 1 | #153 open→release/cycle3-final-rc | Orchestra Issue #151 |
| `agent/152-152-1791051729-ff4ddd3d` | 2026-10-03 | 1 | #154 open→release/cycle3-final-rc | Orchestra Issue #152 |
| `feature/deploy-broker-133` | 2026-10-03 | 6 | #139 open→validation/deploy-broker-133-base | Make Moscow-midnight expiry regression deterministic (#140) |
| `feature/vtb-debt-export-135` | 2026-10-03 | 16 | #136 open→validation/security-80-prod-base | Match localized debt amount in browser regression (#135) |
| `feature/vtb-dryrun-74` | 2026-10-03 | 18 | #137 open→validation/vtb-74-prod-base | Make Moscow-midnight expiry regression deterministic (#140) |
| `orchestra/control-lane-20261003` | 2026-10-03 | 3 | #142 open→main | chore: add non-merge orchestra control lane |
| `release/security-80-prod-v1` | 2026-10-03 | 46 | #129 open→validation/security-80-prod-base | #80 align unsafe-extension regression with security-specific form erro |
| `release/workbench-actions-131` | 2026-10-03 | 13 | #138 open→validation/workbench-actions-131-base | Fix meter-binding E2E strict locator (#131) |

### C. PR влит в другую ветку (в основном feature/water-admin), но коммиты не в прод-линии — нужна сверка по содержимому — 23

| Ветка | Посл. коммит | +к проду | PR | Последнее сообщение |
|---|---|---|---|---|
| `fix/workbench-editor-contrast-125` | 2026-10-02 | 6 | #127 merged→fix/resident-invite-ambiguity-prod | #125 update workbench entry E2E label |
| `feature/admin-panel-integration` | 2026-10-01 | 157 | #120 merged→feature/water-admin | Port read-only workbench to integration without changing security work |
| `chore/roles-guide-sync-integration` | 2026-09-30 | 141 | #104 merged→feature/water-admin | Resolve PR #104 against current staff workspace |
| `fix/issue-114-mixed-role-switch` | 2026-09-30 | 155 | #115 merged→feature/water-admin | Fix #114: drop global mixed-role context processor |
| `fix/security-scanner-stream-v1` | 2026-09-30 | 136 | #113 merged→feature/water-admin | Test scanner command for TCP-compatible streaming |
| `feature/issue-106-simple-workspace` | 2026-09-29 | 131 | #107 merged→feature/water-admin | test: isolate staff water browser sessions |
| `fix/issue-106-query-budget` | 2026-09-29 | 133 | #112 merged→feature/water-admin | perf: avoid duplicate staff water context lookup |
| `fix/resident-request-invite-ambiguity-integration-v1` | 2026-09-29 | 108 | #105 merged→feature/water-admin | fix: bind resident access requests to exact cabinet |
| `docs/reconcile-roadmap-after-access-v2` | 2026-09-28 | 106 | #103 merged→feature/water-admin | docs: reconcile roadmap after access v2 production |
| `feature/issue-97-resident-grants` | 2026-09-28 | 97 | #98 merged→feature/water-admin | access: merge security and granular invite migration branches |
| `feature/issue-99-access-control-v2-foundation` | 2026-09-28 | 101 | #100 merged→feature/water-admin | Fix access center browser expectations |
| `fix/issue-99-production-migration-graph` | 2026-09-28 | 104 | #101 merged→feature/water-admin | Add merge node for security and access v2 migrations |
| `feature/staff-workspace-documents` | 2026-09-27 | 49 | #85 merged→feature/water-admin | Update Staff Workspace release baseline and Documents state |
| `feature/staff-workspace-governance-89` | 2026-09-27 | 73 | #91 merged→feature/water-admin | Fix governance tests to use staff board permissions |
| `fix/issue-86-work-tab-v2` | 2026-09-27 | 58 | #88 merged→feature/water-admin | Fix #86: scope bottom-nav E2E to mobile viewports |
| `fix/issue-92-mfa-password-policy` | 2026-09-27 | 86 | #95 merged→feature/water-admin | Fix MFA profile test to use default TOTP device |
| `fix/issue-92-optional-2fa` | 2026-09-27 | 92 | #96 merged→feature/water-admin | Issue #92: replace legacy mandatory-OTP expectation |
| `security/appeal-attachments-spam-alerts` | 2026-09-27 | 27 | #81 merged→feature/water-admin | Harden legacy appeal admin upload path |
| `chore/zur-36-dependabot` | 2026-09-26 | 1 | #66 merged→feature/water-admin | chore: add bounded weekly Dependabot config |
| `zur-53-board-polls` | 2026-09-24 | 17 | #50 merged→feature/water-admin | ZUR-53: align generated board audit index name |
| `ops/production-status-monitor` | 2026-09-21 | 1 | #17 merged→main | ops: add production status monitor |
| `golubevna/zur-8-deploy-test-utf8` | 2026-09-20 | 1 | #5 merged→feature/water-admin | test: read deploy script as UTF-8 |
| `zur-35-playwright-e2e` | 2026-09-20 | 5 | #4 merged→feature/water-admin | test: prepare Django session before Playwright loop |

### D. PR закрыт без слияния — вероятно брошены/заменены — 10

| Ветка | Посл. коммит | +к проду | PR | Последнее сообщение |
|---|---|---|---|---|
| `feature/vtb-74-dryrun` | 2026-10-03 | 2 | #130 closed→validation/security-80-prod-base | #74 add read-only VTB registry parser and real-sample tests |
| `feature/workbench-meter-access-131` | 2026-10-03 | 7 | #132 closed→validation/security-80-prod-base | #131 add Chromium/WebKit mobile meter-binding acceptance |
| `release/security-80-prod` | 2026-10-03 | 76 | #128 closed→validation/security-80-prod-base | #80 fix private attachment regression expectation |
| `feature/water-admin` | 2026-10-01 | 158 | #1 closed→main | Merge #120: preserve admin workbench in integration |
| `fix/issue-86-work-tab` | 2026-09-27 | 34 | #87 closed→feature/water-admin | Fix #86: cover dedicated Work tab regression |
| `zur-120-nginx-header-scope` | 2026-09-25 | 2 | #59 closed→feature/water-admin | ZUR-120: test scoped nginx headers and migration |
| `golubevna/zur-65-accessibility-gate` | 2026-09-24 | 10 | #37 closed→feature/water-admin | ZUR-65: fix desktop logout contrast |
| `golubevna/zur-66-public-balance-e2e` | 2026-09-24 | 1 | #36 closed→feature/water-admin | ZUR-66: cover public site and water balance in browser E2E |
| `golubevna/zur-63-cache-bust` | 2026-09-23 | 3 | #33 closed→feature/water-admin | ZUR-63: restore approved dashboard photo and stronger login dimming |
| `chore/work-system-agents` | 2026-09-19 | 1 | #3 closed→feature/water-admin | docs: add concise agent working guide |

### E. Без PR — черновики, дубли или ручные release-ветки — 15

| Ветка | Посл. коммит | +к проду | PR | Последнее сообщение |
|---|---|---|---|---|
| `fix/deploy-broker-selfservice-133-20261006` | 2026-10-06 | 4 | — | #133 pin self-service deploy to one promotion ref |
| `fix/131-resident-multimeter` | 2026-10-03 | 15 | — | Keep resident meter lifecycle check in portal UI |
| `fix/143-c-migration-release` | 2026-10-03 | 6 | — | Make Moscow-midnight expiry regression deterministic (#140) |
| `issue143/a-resident-multimeter-v2` | 2026-10-03 | 14 | — | Fix resident multi-meter correctness (#143) |
| `issue143/a-resident-multimeter-v3` | 2026-10-03 | 13 | — | Fix meter-binding E2E strict locator (#131) |
| `issue143/c-migration-aware` | 2026-10-03 | 6 | — | Make Moscow-midnight expiry regression deterministic (#140) |
| `fix/resident-invite-ambiguity-prod` | 2026-10-02 | 7 | — | #125 UI: fix workbench editor contrast and labels |
| `fix/resident-invite-ambiguity-clean` | 2026-09-29 | 1 | — | fix: bind resident access requests to the requesting cabinet |
| `fix/resident-request-invite-ambiguity-v1` | 2026-09-29 | 11 | — | test: reject ambiguous unbound invite approval |
| `design/access-control-v2` | 2026-09-28 | 3 | — | docs: map people, property, water, governance and delegation relations |
| `feature/access-control-v2-foundation` | 2026-09-28 | 11 | — | access v2: keep resolver fixtures within account field limits |
| `feature/staff-governance-89` | 2026-09-27 | 64 | — | Add governance workspace tests for #89 |
| `chore/zur-36-ruff-baseline` | 2026-09-26 | 2 | — | ZUR-36 compare Ruff default with narrow correctness gate |
| `golubevna/zur-60-letter-image-hotfix` | 2026-09-23 | 1 | — | test |
| `main` | 2026-09-21 | 2 | — | Merge pull request #17 from Zursulus/ops/production-status-monitor |

### A. Целиком в production-коммите 6fb32ef — кандидаты на удаление (код уже на проде) — 102

| Ветка | Посл. коммит | +к проду | PR | Последнее сообщение |
|---|---|---|---|---|
| `fix/deploy-broker-v2-133-20261006` | 2026-10-06 | 0 | — | #159 restore workbench contrast browser regression |
| `fix/workbench-contrast-regression-20261006` | 2026-10-06 | 0 | #160 open→fix/release-public-atomic-20261005 | #159 restore workbench contrast browser regression |
| `fix/deploy-broker-bootstrap-20261005` | 2026-10-05 | 0 | #158 closed→fix/release-public-atomic-20261005 | Use service context inside rehearsal steps and preserve helper executa |
| `fix/night-ui-readiness-20261004` | 2026-10-05 | 0 | #155 open→release/143-cumulative-rc | Scope staff moderation queues by finalization authority |
| `fix/ui-flow-polish-20261005` | 2026-10-05 | 0 | #156 open→fix/night-ui-readiness-20261004 | test: await access approval POST navigation |
| `release/143-cumulative-rc` | 2026-10-03 | 0 | #149 open→validation/cycle3-final-rc-base | #143 cumulative release candidate |
| `feature/staff-registry-editor-125` | 2026-10-02 | 0 | #126 merged→fix/resident-invite-ambiguity-prod | #125 keep cross-browser editor search fixture stable |
| `release/cycle3-final-rc` | 2026-10-02 | 0 | — | #125 Staff Workspace: direct personal/contact data editor |
| `release/security-80-prod-final` | 2026-10-02 | 0 | — | #125 Staff Workspace: direct personal/contact data editor |
| `validation/cycle3-final-rc-base` | 2026-10-02 | 0 | — | #125 Staff Workspace: direct personal/contact data editor |
| `validation/deploy-broker-133-base` | 2026-10-02 | 0 | — | #125 Staff Workspace: direct personal/contact data editor |
| `validation/security-80-prod-base` | 2026-10-02 | 0 | — | #125 Staff Workspace: direct personal/contact data editor |
| `validation/vtb-74-prod-base` | 2026-10-02 | 0 | — | #125 Staff Workspace: direct personal/contact data editor |
| `validation/workbench-actions-131-base` | 2026-10-02 | 0 | — | #125 Staff Workspace: direct personal/contact data editor |
| `feature/admin-panel-ci-recovery` | 2026-10-01 | 0 | #119 merged→fix/resident-invite-ambiguity-prod | Preserve search while following links and simplify resident access lab |
| `feature/admin-resident-release-121` | 2026-10-01 | 0 | #122 merged→fix/resident-invite-ambiguity-prod | Block legacy fallback after explicit resident grant ends (#121) |
| `fix/resident-document-filters-121` | 2026-10-01 | 0 | #124 merged→fix/resident-invite-ambiguity-prod | Consume streaming test response through Django client wrapper |
| `fix/staff-workspace-logout-121` | 2026-10-01 | 0 | #123 merged→fix/resident-invite-ambiguity-prod | fix: make staff workspace logout available without resident access |
| `validation/staff-registry-editor-125-base` | 2026-10-01 | 0 | — | Merge pull request #124 from Zursulus/fix/resident-document-filters-12 |
| `release/issue-114-mixed-role-prod` | 2026-09-30 | 0 | #116 merged→fix/resident-invite-ambiguity-prod | Fix #114: drop global production mixed-role context |
| `feature/internal-roles-guide-v1` | 2026-09-29 | 0 | — | test: cover internal web roles guide |
| `fix/combined-role-ux-prod` | 2026-09-29 | 0 | #108 merged→fix/resident-invite-ambiguity-prod | test: cover mixed resident staff login guidance |
| `fix/issue-106-simple-workspace-prod` | 2026-09-29 | 0 | #111 merged→fix/resident-invite-ambiguity-prod | test: align legacy work-tab e2e with simplified navigation |
| `fix/line-senior-self-review-policy-prod` | 2026-09-29 | 0 | #110 merged→fix/resident-invite-ambiguity-prod | test: expect roles guide v1.1 |
| `fix/line-senior-self-review-prod` | 2026-09-29 | 0 | — | Merge #108: mixed resident/staff UX |
| `ci/issue-99-production-base` | 2026-09-28 | 0 | — | Release #97: granular resident access production backport |
| `release/issue-97-production` | 2026-09-28 | 0 | — | Release #97: granular resident access production backport |
| `release/issue-99-production` | 2026-09-28 | 0 | #102 closed→ci/issue-99-production-base | Release #99: include E2E contract in guarded allowlist |
| `chore/github-workflow-reorg` | 2026-09-27 | 0 | #77 merged→feature/water-admin | docs: separate roadmap from operational tracking |
| `docs/staff-workspace-architecture-v1` | 2026-09-27 | 0 | #68 merged→feature/water-admin | Update canonical plan for staff workspace phase |
| `feature/staff-workspace-access` | 2026-09-27 | 0 | #79 merged→feature/water-admin | Update roadmap through Staff Workspace Access |
| `feature/staff-workspace-appeals` | 2026-09-27 | 0 | #71 merged→feature/water-admin | Fix appeals workspace regression assertions |
| `feature/staff-workspace-finance` | 2026-09-27 | 0 | #72 merged→feature/water-admin | Keep finance E2E ORM outside Playwright sync context |
| `feature/staff-workspace-slice-1` | 2026-09-27 | 0 | #69 merged→feature/water-admin | Make water table keyboard-scrollable |
| `feature/staff-workspace-water` | 2026-09-27 | 0 | #70 merged→feature/water-admin | Fix water hub action selector in browser smoke |
| `fix/admin-return-to-work` | 2026-09-27 | 0 | #83 merged→feature/water-admin | Match admin return-link regression to actual label |
| `release/documents-84-clean` | 2026-09-27 | 0 | — | Prepare clean Documents release without security hardening |
| `release/documents-worktab-84-86-clean` | 2026-09-27 | 0 | — | Release #84/#86: run clean Work-tab mobile E2E |
| `release/governance-89-clean` | 2026-09-27 | 0 | — | Release #89: clean governance Staff Workspace |
| `release/issue-92-optional-2fa` | 2026-09-27 | 0 | — | Release #92: allow legacy-policy test update |
| `release/issue-92-prod-hotfix` | 2026-09-27 | 0 | — | Release #92: fix production password-policy smoke check |
| `security/appeal-attachments-spam-alerts-v2` | 2026-09-27 | 0 | — | Merge pull request #79 from Zursulus/feature/staff-workspace-access |
| `security/appeal-attachments-spam-alerts-v3` | 2026-09-27 | 0 | — | Merge pull request #79 from Zursulus/feature/staff-workspace-access |
| `security/appeal-attachments-spam-alerts-v4` | 2026-09-27 | 0 | — | Merge pull request #79 from Zursulus/feature/staff-workspace-access |
| `security/appeal-attachments-spam-alerts-v5` | 2026-09-27 | 0 | — | Merge pull request #79 from Zursulus/feature/staff-workspace-access |
| `security/appeal-attachments-spam-alerts-v6` | 2026-09-27 | 0 | — | Merge pull request #79 from Zursulus/feature/staff-workspace-access |
| `fix/appeals-tabs-mobile` | 2026-09-26 | 0 | #67 merged→feature/water-admin | test(portal): cover appeal status tabs |
| `zur-201-reading-reconciliation` | 2026-09-26 | 0 | #64 merged→feature/water-admin | fix(water): avoid locking nullable joined account |
| `zur-267-password-reset` | 2026-09-26 | 0 | #65 merged→feature/water-admin | fix(ui): preserve Russian password reset labels |
| `zur-112-installer-fix` | 2026-09-25 | 0 | #54 merged→feature/water-admin | ZUR-112 avoid pipefail false negative in nginx verification |
| `zur-112-portal-failure-sweep` | 2026-09-25 | 0 | #53 merged→feature/water-admin | ZUR-112 cover all resident portal routes in browser |
| `zur-115-admin-sweep` | 2026-09-25 | 0 | #55 merged→feature/water-admin | ZUR-115 add full admin GET smoke coverage |
| `zur-115-admin-upload-limit` | 2026-09-25 | 0 | #56 merged→feature/water-admin | ZUR-115: cover oversized admin import rejection |
| `zur-120-journald-retention` | 2026-09-25 | 0 | #58 merged→feature/water-admin | ZUR-120: test journald retention rendering |
| `zur-120-nginx-header-scope-v2` | 2026-09-25 | 0 | #60 merged→feature/water-admin | ZUR-120: test scoped nginx headers and migration |
| `zur-120-nginx-security-headers` | 2026-09-25 | 0 | #57 merged→feature/water-admin | ZUR-120: test nginx security headers transform |
| `zur-129-performance-baseline` | 2026-09-25 | 0 | #61 merged→feature/water-admin | ZUR-129: tighten dashboard query budget |
| `zur-136-business-integrity-audit` | 2026-09-25 | 0 | #62 merged→feature/water-admin | ZUR-136: test aggregate integrity audit |
| `zur-136-guarded-deploy` | 2026-09-25 | 0 | #63 merged→feature/water-admin | ZUR-136: test guarded audit deploy |
| `golubevna/zur-54-charge-obligation-foundation` | 2026-09-24 | 0 | #44 merged→feature/water-admin | ZUR-54: fix payer constraint class scope |
| `golubevna/zur-54-finance-reporting` | 2026-09-24 | 0 | #45 merged→feature/water-admin | ZUR-54: cover finance report privacy and balances |
| `golubevna/zur-54-person-plot-obligation` | 2026-09-24 | 0 | #47 merged→feature/water-admin | ZUR-54: cover explicit person and plot reporting |
| `golubevna/zur-54-resident-finance-context` | 2026-09-24 | 0 | #46 merged→feature/water-admin | ZUR-54: cover resident charge status and privacy |
| `golubevna/zur-63-reference-exact` | 2026-09-24 | 0 | #35 merged→feature/water-admin | ZUR-63: target visible mobile controls in reference e2e flow |
| `golubevna/zur-65-accessibility-gate-refresh` | 2026-09-24 | 0 | #43 merged→feature/water-admin | ZUR-65: refresh accessibility gate on current feature base |
| `golubevna/zur-66-public-balance-e2e-refresh` | 2026-09-24 | 0 | #42 merged→feature/water-admin | ZUR-66: refresh public and balance E2E on current base |
| `golubevna/zur-68-identity-membership` | 2026-09-24 | 0 | #38 merged→feature/water-admin | ZUR-68: cover identity and membership boundaries |
| `golubevna/zur-69-access-request` | 2026-09-24 | 0 | #39 merged→feature/water-admin | ZUR-69: use stable resident access role values in tests |
| `golubevna/zur-70-portal-access-resolver` | 2026-09-24 | 0 | #40 merged→feature/water-admin | ZUR-70: cover historical appeal authority |
| `golubevna/zur-71-registry-person-dry-run` | 2026-09-24 | 0 | #41 merged→feature/water-admin | ZUR-71: use canonical preseeded resident slots in tests |
| `zur-53-deploy` | 2026-09-24 | 0 | #51 merged→feature/water-admin | ZUR-53: validate guarded deploy script in CI |
| `zur-64-line-controller-water` | 2026-09-24 | 0 | #48 merged→feature/water-admin | ZUR-64: make scoped workspace E2E locator unambiguous |
| `zur-96-cumulative-production-release` | 2026-09-24 | 0 | #49 merged→feature/water-admin | ZUR-96: validate cumulative deploy script in CI |
| `golubevna/zur-57-deploy-resident-portal` | 2026-09-23 | 0 | #21 merged→feature/water-admin | ZUR-57: validate resident portal deploy script in CI |
| `golubevna/zur-58-resident-numbers` | 2026-09-23 | 0 | #22 merged→feature/water-admin | ZUR-58: validate guarded number deploy in CI |
| `golubevna/zur-59-private-registry` | 2026-09-23 | 0 | #23 merged→feature/water-admin | ZUR-59: validate private registry deploy script in CI |
| `golubevna/zur-60-letter-hotfix-v2` | 2026-09-23 | 0 | #25 merged→feature/water-admin | ZUR-60: guarded hotfix для доступного письма |
| `golubevna/zur-60-public-home-letter` | 2026-09-23 | 0 | #24 merged→feature/water-admin | ZUR-60: allow frontend regression test in guarded deploy |
| `golubevna/zur-61-explicit-legacy-map` | 2026-09-23 | 0 | #29 merged→feature/water-admin | ZUR-61: test explicit legacy account mapping |
| `golubevna/zur-61-guarded-deploy` | 2026-09-23 | 0 | #27 merged→feature/water-admin | ZUR-61: pin deploy workflow validation |
| `golubevna/zur-61-import-reestra-1300-i-podhotovka-testovoho-polzovatelya-333` | 2026-09-23 | 0 | #26 merged→feature/water-admin | ZUR-61: test private registry importer |
| `golubevna/zur-61-reserve-305` | 2026-09-23 | 0 | #28 merged→feature/water-admin | ZUR-61: validate reserve 305 deploy script |
| `golubevna/zur-62-static-permissions` | 2026-09-23 | 0 | #30 merged→feature/water-admin | ZUR-62: test public static and private upload permission split |
| `golubevna/zur-63-full-redesign` | 2026-09-23 | 0 | #34 merged→feature/water-admin | ZUR-63: remove obsolete layered polish stylesheet |
| `golubevna/zur-63-visual-polish` | 2026-09-23 | 0 | #31 merged→feature/water-admin | ZUR-63: visual polish resident portal to approved mobile-first concept |
| `golubevna/zur-63-visual-round2` | 2026-09-23 | 0 | #32 merged→feature/water-admin | ZUR-63: load screenshot-driven polish stylesheet |
| `golubevna/zur-56-pererabotat-lichnii-kabinet-chlena-tsn-v-druzhelyubnii` | 2026-09-22 | 0 | #20 merged→feature/water-admin | ZUR-56: accept stricter private cache headers |
| `codex/zur-40-controller-mobile` | 2026-09-21 | 0 | #7 merged→feature/water-admin | test: fix exact controller date button match |
| `feature/zur-41-admin-navigation` | 2026-09-21 | 0 | #9 merged→feature/water-admin | ZUR-41: update controller e2e for simplified dashboard |
| `feature/zur-41-moderation-ux` | 2026-09-21 | 0 | #8 merged→feature/water-admin | Fix ZUR-41 moderation e2e confirmation text |
| `feature/zur-42-private-data-write` | 2026-09-21 | 0 | #12 merged→feature/water-admin | test: зафиксировать writable path для runtime uploads |
| `feature/zur-42-public-content` | 2026-09-21 | 0 | #10 merged→feature/water-admin | ZUR-42: проверить ролевую навигацию публичного контента |
| `feature/zur-42-public-root-fix` | 2026-09-21 | 0 | #11 merged→feature/water-admin | ZUR-42: учесть regression-тест public root в allowlist |
| `feature/zur-43-mobile-polish` | 2026-09-21 | 0 | #14 merged→feature/water-admin | ZUR-43: improve mobile balance layout |
| `feature/zur-43-water-balance` | 2026-09-21 | 0 | #13 merged→feature/water-admin | ZUR-43: добавить regression-тесты водного баланса |
| `feature/zur-46-cache-bust` | 2026-09-21 | 0 | #16 merged→feature/water-admin | ZUR-46: обновить cache key app.js |
| `feature/zur-46-urgent-news-popup` | 2026-09-21 | 0 | #15 merged→feature/water-admin | ZUR-46: auto-open featured public news once per session |
| `feature/zur-47-first-smart-article` | 2026-09-21 | 0 | #19 merged→feature/water-admin | ZUR-47: проверить редакционный интерфейс публичных материалов |
| `feature/zur-47-smart-news` | 2026-09-21 | 0 | #18 merged→feature/water-admin | ZUR-47: оформить блок умных текстов о главном |
| `codex/zur-10-finance` | 2026-09-20 | 0 | #6 merged→feature/water-admin | test: cover ZUR-10 finance core |
| `feature/zur-12-water-import-package` | 2026-09-18 | 0 | #2 merged→feature/water-admin | ZUR-12: expose package dry-run inside protected admin |
| `backup/production-clean-2026-09-16` | 2026-09-16 | 0 | — | Show latest group water total on account cards |

## Другие места работы ChatGPT

- **Компьютер владельца:** `Рабочий стол\ChatGPT-work\zur157` — локальная копия другого репозитория
  `Zursulus/tg-bot-server-recovery` (Telegram-бот планирования домашних задач), ветка `golubevna/zur-157-tree-pagination`.
  Доступа к этому репозиторию у Claude пока нет.
- **Рабочий стол:** архивы-передачи ChatGPT (`*_HANDOFF.zip`, `*.zip.b64`), Excel по воде и реестру ТСН,
  скриншоты проверки Telegram-бота.
- **Notion:** WORK SYSTEM (~180 тыс. знаков правил), PROJECT CACHE INDEX, PORTFOLIO DISPATCH LEDGER,
  Задачник, Knowledge Hub Труд-1 (PROJECT STATE ~200 тыс. знаков, Operating Charter, правовая база 217-ФЗ,
  редакционный стандарт, историческое досье, архив Linear).
- **ChatGPT Library** — закрытые исходники (фото тарифов, письма ВТБ и т.п.). Доступа у Claude нет.
- **Linear** — архив до миграции, ChatGPT его больше не использует.
- Другие проекты портфеля: Автономия человека, Самоопределение, Частоты, tg-bot, Orchestra, Каббала.

## Что предлагаю дальше (решает владелец)

1. Назначить одну каноническую ветку = production (`6fb32ef`) и сделать её default вместо устаревшего `main`.
2. Сверить `feature/water-admin` с продом по содержимому; отметить, что не перенесено.
3. Удалить 102 ветки из категории A (их код уже в проде) — только после подтверждения владельца.
4. Разобрать 15 открытых PR: закрыть заменённые, оставить живые.
