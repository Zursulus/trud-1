# Труд-1: продуктовый и технический roadmap

Юридическое наименование: ТСН «ТРУД-1». Локация: Орджоникидзе, Крым.

Этот файл хранит направление продукта, последовательность крупных этапов и постоянные инварианты. Он **не является оперативным task tracker**. Исторические версии остаются в Git history.

## Источники истины

1. GitHub Issues / PR — незавершённые обязательства, acceptance criteria и реализация.
2. GitHub commits / CI — техническая история и доказательство проверок.
3. Production `trud-1.ru` deployment marker/status — фактически установленная версия. Push/merge не равен deploy.
4. `docs/PLAN.md` и архитектурные документы — roadmap и устойчивые правила.
5. Notion — долговременные решения/rationale и короткий recovery checkpoint; не второй task tracker.
6. Drive / Library — исходные Excel и крупные/закрытые артефакты.
7. Linear — read-only исторический архив работ до перехода на GitHub Issues.

## Состояние продукта — 28 сентября 2026

Ядро рабочей системы реализовано: реестр участков/людей/связей, водоучёт и баланс, безопасный импорт, закрытый реестр ПД, кабинет жителя, обращения, документы, начисления/оплаты/долги, предварительные опросы правления, публичный контент, backup/recovery, guarded deploy/rollback и CI с browser E2E/accessibility.

Staff Workspace v1 завершён. Все семь запланированных vertical slices работают через `/work/`; `/admin/` остаётся техническим fallback.

Доступы после исходного Access-slice развиты до единой Person-centric модели Access Control V2:

`Identity → Assignment → Capabilities → Scope → Validity → Audit`

Один человек может одновременно иметь личный кабинет и несколько служебных назначений с разными scope. Бизнес-статусы человека сами по себе права не создают. Подробности и инварианты: `docs/ACCESS_CONTROL_V2.md`.

## Staff Workspace v1 — завершённая фаза

Цель фазы — отдельная ежедневная «Рабочая база» сотрудников на `/work/` без удаления технического Django Admin.

Реализованные vertical slices:

1. Foundation / Search / Account — PR #69.
2. Water — PR #70.
3. Appeals — PR #71.
4. Finance — PR #72.
5. Access — PR #79.
6. Documents/content — Issue #84 / PR #85.
7. Governance/polls — Issue #89 / PR #91.

После v1 доступ был дополнительно развит отдельными завершёнными задачами:

- Issue #97 / PR #98 — точечные права жителя через `ResidentIdentity + PortalGrant`; Production Done.
- Issue #99 / PR #100 — единый Access Control V2; Production Done.
- PR #101 — канонический migration graph, совместимый с production, где security migration пока не установлена.

Полные продуктовые границы Staff Workspace v1: `docs/STAFF-WORKSPACE-ARCHITECTURE.md`.

Нового «восьмого slice» из старой последовательности нет. Следующее существенное направление должно возникать из отдельной READY-задачи с реальным пользователем/процессом и acceptance criteria, а не из продолжения нумерации v1.

## Release baseline

Текущий подтверждённый production SHA:

`0b71d78240eb30a00f5d3d210d89e9afee4e65fd`

Это controlled production release Issue #99, построенный от предыдущего production без заблокированного security-контура #80. В production применена `water.0032_access_control_v2_assignments`; deployment marker и HTTP smoke подтверждены после установки.

Текущий integration `feature/water-admin` намеренно не равен production: в integration присутствует ранее merged security hardening PR #81 / Issue #80, включая `SecurityAlert` и ClamAV prerequisite. После PR #101 канонический migration graph допускает безопасное последующее объединение ветвей миграций, но **integration HEAD всё равно нельзя выкатывать wholesale**, пока не снят инфраструктурный blocker #80 и не выполнен отдельный release gate.

### Security hardening #80 — BLOCKED_BY_INFRA_CAPACITY

PR #81 merged в integration, но production release отложен. Реализованы безопасные PDF/JPG/PNG/DOCX/XLSX, структурная проверка OOXML, anti-spam/rate limits, SecurityAlert и fail-closed malware scan.

Текущий production-хост не имеет безопасного ресурсного запаса для постоянного локального ClamAV daemon в ранее проверенной конфигурации. Требование fail-closed не ослаблять ради deploy.

Снять blocker можно только одним из двух путей:

1. увеличить production capacity и пройти prerequisite/smoke локального scanner;
2. отдельно спроектировать, защитить и проверить private/authenticated scanner architecture.

До этого #80 остаётся открытым, а будущие production releases обязаны явно исключать его изменения.

## Текущая продуктовая очередь: внешние / инфраструктурные gates

На 28.09.2026 среди канонических открытых задач Труд-1 нет READY leaf, которую можно честно довести до Production Done без нового внешнего входа.

### Банковский обмен ВТБ — #74

`WAITING_REAL_BANK_SAMPLE`.

Начинать реализацию только после получения реального формата/образца ВТБ, используемого ТСН. Сначала read-only parser/dry-run, затем правила сопоставления/idempotency/audit, затем controlled import подтверждённых платежей. Формат не придумывать.

### Зарегистрированный устав — #75

`WAITING_REGISTERED_CHARTER`.

Получить и проверить актуальный зарегистрированный устав до автоматизации действительно юридически значимых процедур. Внутренние предварительные опросы правления этим не блокируются.

Общее голосование жителей / членов ТСН сейчас **не реализуется**. В продукте остаётся только существующий предварительный, неофициальный workflow `BoardPoll/BoardVote` для активного состава правления. Любое расширение до общего/юридически значимого голосования — отдельная будущая задача после проверки правовой основы.

### Историко-территориальные первоисточники — #76

`WAITING_EXTERNAL_PRIMARY_DOCS`.

Официальная выписка ФНС уже подтверждает регистрационный № `22283197`, дату `27.05.1994` и регистрирующий орган — Исполнительный комитет Феодосийского городского совета. Постановление Администрации Феодосии №1573 от 07.05.2025 подтверждает современную земельную цепочку через договор №97 от 29.12.2022.

Для исходных документов поиск адресно направлен в фонды муниципального архива Феодосии:

- фонд 1 — Феодосийский городской совет и его исполнительный комитет, 1973–2015;
- фонд 2 — Орджоникидзевский поселковый совет и его исполнительный комитет, 1982–2014;
- фонд 78 — Феодосийское городское управление земельных ресурсов, 1996–2014.

Первичные документы 1994 года, исходный акт землеотвода и полный договор №97 с приложениями пока не получены. Результат исследования хранится в Notion с provenance; неподтверждённые вторичные сведения не превращаются в факты сайта.

### Appeal security — #80

`BLOCKED_BY_INFRA_CAPACITY`.

Код реализован и проверен в integration, но production deployment запрещён до появления безопасного malware-scanning контура. Подробности выше.

### 1С

Проектировать только после банковского обмена и фиксации фактического финансового процесса.

## Архитектурные правила Staff Workspace

- `/work/` строится вокруг пользовательских задач и процессов, не вокруг Django-моделей.
- `/admin/` не удаляется и остаётся аварийным/техническим интерфейсом.
- Общая бизнес-логика выносится в service/use-case слой по мере переноса workflow; дублирование правил запрещено.
- Не делать SPA, микросервисы или отдельный REST API только ради UI.
- Не вводить новые роли без реального пользователя и отдельного процесса.
- Capability и object scope проверяются server-side; скрытие UI не считается защитой.
- Роль — preset, а не единственный источник истины; несколько назначений одного Person могут безопасно композиционироваться.
- Бизнес-статусы `TsnMembership`, `BoardMembership`, ownership и совпадение ФИО/email/телефона не создают системных прав автоматически.
- Обычный staff workspace не расширяет границу персональных данных.
- Каждый существенный vertical slice должен оставлять трассу Issue → branch/PR → CI → merge SHA → production marker/smoke, если production применим.

## Постоянные инварианты данных

- Участок, человек, пользователь входа, членство и лицевой счёт — разные сущности.
- Внутренний ID `TRUD-PLOT-*` не является автоматически номером участка или банковским лицевым счётом.
- Исторические связи собственника/представителя не переписываются задним числом.
- Не придумывать отсутствующие показания, даты, участки и соответствия.
- Неоднозначные строки импорта сохранять для ручной проверки.
- Формульные итоги Excel не считать первичными данными.
- Поле «Оплата по показаниям» не считать подтверждённым платежом без отдельного источника.
- Групповые кубы и потери не распределять автоматически без утверждённого правила.
- GitHub не должен содержать реальные телефоны, персональные/банковские данные, секреты или backup.
- Production не использовать для отладки.

## Release policy

1. Существенная незавершённая работа имеет GitHub Issue с целью/scope/acceptance criteria.
2. Код меняется через минимальную branch/PR единицу; история commits/PR сохраняется для regression analysis.
3. До merge проходят применимые tests/CI.
4. Перед production сверяются deployed SHA, target diff, migrations/requirements/settings/ops changes и backup/rollback readiness.
5. Deploy выполняется controlled-процедурой; несовместимые изменения получают отдельный reviewed deploy plan.
6. После deploy проверяются service/marker, HTTP smoke и затронутый пользовательский сценарий.
7. Production-задача закрывается только после production evidence.

## Definition of Done

Для работы применяются два фактических уровня:

- **Implementation Done** — acceptance criteria реализации выполнены, применимые tests/CI зелёные, изменение merged.
- **Production Done** — дополнительно установлен нужный SHA и пройдены обязательные production smoke/acceptance checks.

История не удаляется и не переписывается ради «чистоты»: при регрессии должна восстанавливаться цепочка `симптом → production SHA → merge commit → PR → Issue/исторический Linear context → diff`.

## Технические ссылки

- `docs/STAFF-WORKSPACE-ARCHITECTURE.md` — Staff Workspace architecture/acceptance criteria.
- `docs/ACCESS_CONTROL_V2.md` — Access Control V2: capabilities/scopes/roles/delegation/audit.
- `backend/README.md` — модель и backend invariants.
- `docs/ROLES.md` — роли и аудит.
- `docs/PRIVACY.md` — privacy model.
- `docs/MFA.md` — MFA/recovery.
- `ops/DEPLOYMENT.md` — deploy/rollback.
- `ops/RECOVERY.md` — backup/recovery.

Обновлено: 2026-09-28.
