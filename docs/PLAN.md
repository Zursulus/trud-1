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

## Состояние продукта — 27 сентября 2026

Ядро рабочей системы реализовано: реестр участков/людей/связей, водоучёт и баланс, безопасный импорт, роли/object scope/MFA, закрытый реестр ПД, кабинет жителя, обращения/документы/доступы, начисления/оплаты/долги, опросы правления, публичный контент, backup/recovery, guarded deploy/rollback и CI с browser E2E/accessibility.

## Текущая фаза: Staff Workspace v1

Цель — отдельная ежедневная «Рабочая база» сотрудников на `/work/`; `/admin/` остаётся техническим fallback.

Реализовано в интеграционной ветке `feature/water-admin`:

1. Foundation / Search / Account — PR #69.
2. Water — PR #70.
3. Appeals — PR #71.
4. Finance — PR #72.

Следующие продуктовые slices после установления актуального production baseline:

5. Access.
6. Documents/content.
7. Governance/polls.

Полные продуктовые границы и acceptance criteria: `docs/STAFF-WORKSPACE-ARCHITECTURE.md`.

## Release baseline

На 2026-09-27 проверено:

- integration HEAD: `b413a436c7895cbc6292ce6e53767b7ed2db67ea`;
- CI Water admin run #1080 для этого HEAD: success;
- production marker: `96665e534bb2994b650d60a06b7c21630f457f84`, deployed `2026-09-26T20:15:14Z`;
- integration branch на 83 commits впереди production;
- текущая оперативная release-задача ведётся в GitHub Issue #73.

До controlled deploy и production smoke новые Staff Workspace slices не считаются production-выпущенными.

## Будущие / внешне заблокированные направления

### Банковский обмен ВТБ

GitHub Issue #74. Начинать только после получения реального формата/образца банка. Сначала read-only parser/dry-run, затем правила сопоставления/idempotency/audit, затем controlled import подтверждённых платежей. Формат не придумывать.

### Юридически значимые процедуры

GitHub Issue #75 хранит необходимость получить и проверить актуальный зарегистрированный устав до автоматизации процессов, которые действительно от него зависят. Внутренние неофициальные опросы правления этим не блокируются.

### Историко-территориальные первоисточники

GitHub Issue #76 хранит незавершённый поиск первичных документов создания и землеотвода. Результат исследования фиксируется в Notion с provenance источников; неподтверждённые вторичные сведения не превращаются в факты сайта.

### 1С

Проектировать только после банковского обмена и фиксации фактического финансового процесса.

## Архитектурные правила Staff Workspace

- `/work/` строится вокруг пользовательских задач и процессов, не вокруг Django-моделей.
- `/admin/` не удаляется и остаётся аварийным/техническим интерфейсом.
- Общая бизнес-логика выносится в service/use-case слой по мере переноса workflow; дублирование правил запрещено.
- Не делать SPA, микросервисы или отдельный REST API только ради UI.
- Не вводить новые роли без реального пользователя и отдельного процесса.
- Permission и object scope проверяются server-side; скрытие UI не считается защитой.
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
- `backend/README.md` — модель и backend invariants.
- `docs/ROLES.md` — роли и аудит.
- `docs/PRIVACY.md` — privacy model.
- `docs/MFA.md` — MFA/recovery.
- `ops/DEPLOYMENT.md` — deploy/rollback.
- `ops/RECOVERY.md` — backup/recovery.

Обновлено: 2026-09-27.
