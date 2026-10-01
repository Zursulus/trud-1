# Admin ↔ Resident: контракт выпуска и приёмки v1.1

GitHub #121. База: production `179c25405de75c2b37f0fab3cf2880f33168dc47`.
Статус: implementation candidate; окончательная готовность требует evidence точного SHA, закрытия продуктовых решений и production-приёмки.

## Граница продукта

Включены существующие ежедневные процессы: подтверждённый личный доступ, обращения, передача/сверка воды, ручные начисления и зарегистрированные оплаты, публикация разрешённых документов/новостей и внутренние состояния внимания. Сотрудники работают через `/work/`, технический fallback — `/admin/`. Панель `/work/panel/` остаётся обзорной.

Автоматический ВТБ/эквайринг (#74), официальный устав/ЕСИА (#75), первичные архивные документы (#76) и новый security release #80 имеют собственные внешние gates. Тесты приватной доставки файлов не доказывают отсутствие вредоносного содержимого. До проверки trusted signatures/scanner нельзя объявлять #80 готовым или разворачивать integration wholesale.

Термин «всё проверено» относится только к перечисленным действиям и переходам на конкретном SHA. Количество тестов, code coverage и готовый harness не заменяют Product Done.

## Постоянные правила

- User, Person, связь с участком, членство, личное право и служебное назначение — отдельные сущности. Владение/контакт не создают login или полномочие.
- Принятое решение #92 сохраняется: второй фактор сотрудника необязателен. Штатный парольный вход и существующий OTP поддерживаются; resident login не принимает служебную учётку. Новые тесты не подставляют verified cookie.
- Одна общая сессия совмещённой учётки допускает две вкладки. Полномочие определяют endpoint, действие и объект; посещение личного UI не отзывает служебные права.
- Личный кабинет проверяет личное право. Staff/superuser сам по себе не открывает чужой кабинет. Обращение принадлежит конкретному автору User; второй житель того же счёта не получает его переписку.
- Для обращений, финансов и документов служебные списки/суммы/файлы ограничены ACCOUNT/ALL. Действие повторно проверяет свой capability и объект в application service. Глобальное право Django остаётся отдельным источником ALL.
- Оба центра управления доступами требуют глобальных служебных полномочий: они показывают общую историю и целые логины. ACCOUNT/PERSON назначения не открывают эти операции. Их будущий ограниченный интерфейс требует отдельного контракта; не считать его реализованным.
- Удаление/перепривязка истории не служит исправлением. Автор, счёт, исходное сообщение сохраняются; изменение attribution/context обращения повторно проверяет право.
- Прекращение доступа блокирует последующие resident-запросы и старые формы. Уже созданное обращение продолжает обрабатываться уполномоченным сотрудником, в том числе после отключения login автора.
- Повтор, отказ в доступе, исключение транзакции и конкурирующие операции не создают лишний конечный эффект. Отказ проверяется вместе с неизменностью данных и истории.

## Проверяемые цепочки

| ID | Действия / ожидаемый результат | Доказательство |
|---|---|---|
| AR-01 | Реальные отдельные staff/resident logins; одна dual-role учётка в двух вкладках | `admin_resident_e2e_tests.AdminResidentBrowserTests` — Chromium + WebKit, mobile + desktop, CSRF, оба интерфейса |
| AR-02 | Приглашение сотрудника → активация → identity/grant → реальный повторный вход → одноразовость → снятие прав при открытой сессии | `test_admin_resident_scope.ServiceAccountScopeTests.test_granular_invite_activation_real_login_and_rights_removal`; существующие invite expiry/ambiguity tests в `water.tests` и `test_staff_workspace_access` |
| AR-03 | Resident create → staff request → resident clarification → final response → close | `test_admin_resident_contract.AdminResidentContractTests.test_complete_conversation_has_both_parties_and_exact_audit` + AR-01 |
| AR-04 | Другой житель того же/чужого счёта; staff A не читает/закрывает/скачивает B; истёкшая форма и разные capabilities | `test_shared_account_does_not_share_private_conversation`, `test_scoped_staff_cannot_read_reply_close_or_download_foreign_appeal`, `test_granular_view_does_not_authorize_appeal_or_foreign_reattribution` |
| AR-05 | Завершение права/отключение автора не мешает staff resolution; отказ CSRF; rollback незавершённого staff reply | `test_granular_grant_without_legacy_access_can_complete_conversation`, `test_disabled_author_does_not_strand_staff_resolution`, `test_real_resident_login_preserves_csrf_denial`, `test_failed_staff_transition_rolls_back_message_and_history` |
| AR-06 | Два независимых DB connections одновременно закрывают resolved appeal → один terminal history event | `test_admin_resident_contract.AppealConcurrencyContractTests`; обязателен PostgreSQL, SQLite SKIP не считается PASS |
| AR-07 | Исходное 100; передача 119→120 обновляет одно наблюдение; line review → staff finalize → resident видит 120; repeat не создаёт вторую Reading | `test_water_submission_review_finalization_and_resident_result`; существующие `test_observation_workflow`, `test_controller_workspace`, `staff_workspace_water_e2e_tests` |
| AR-08 | Норматив 20 × тариф 10 = 200; draft не виден в долге; approval → 200; pending payment 150 без эффекта; confirm/allocate → 50; повтор без дубля; reversal → 200 с сохранением allocation | `test_finance_partial_payment_repeat_and_reversal_oracle`; существующие `test_staff_workspace_finance`, `staff_workspace_finance_e2e_tests` |
| AR-09 | Finance A: bounded sums/period/list/form; direct B URL/POST/service denied; разрешённый A действительно работает | `test_admin_resident_scope.ServiceAccountScopeTests.test_finance_lists_forms_actions_and_directory_do_not_escape_account` |
| AR-10 | Document A: bounded list/form/card/download; B denied до открытия файла; metadata service проверяет объект | `test_document_scope_applies_before_file_open_and_metadata_mutation` |
| AR-11 | Staff publish → exact resident bytes + attention → unpublish → old URL 404; featured news publish/unpublish меняет attention | `test_staff_publication_resident_download_attention_and_unpublication`; существующие publication/public feed/browser tests |
| AR-12 | Scoped staff не входит в глобальный центр доступа и не меняет чужой grant даже через service | `test_access_global_center_and_services_fail_closed_for_scoped_actor`; стандартные ALL роли — `test_staff_workspace_access`, `test_access_workflow` |
| AR-13 | Две оплаты по 150 при долге 200: manual/manual отклоняет второй ввод 150; automatic/automatic даёт 150+50; mixed допускает только два корректных последовательных результата; stale parent не обходит отмену | `test_payment_concurrency_contract`; три гонки требуют PostgreSQL. Baseline `8d92592` воспроизвёл два сохранённых ручных зачёта; исправление требует нового green CI |
| AR-14 | Одно приглашение активируется одновременно двумя клиентами → 302/410, одна identity/grant/User; два разных модератора принимают одно показание → одна Reading 120 и одна финальная история | `test_access_water_concurrency_contract`; реальные HTTP login/CSRF, отдельные соединения PostgreSQL |

AR-09/10 также проверяют раздельные create B / view A: ни одна форма не предлагает скрытый B и отказ не создаёт запись/историю. Глобальное утверждение периода учитывает скрытые draft B при ограниченном finance view A; снятие последнего draft допускает существующее глобальное полномочие.

Денежные/водные expected values и список пяти событий истории заданы литералами, независимо от production helpers. Browser fixtures используют canonical PortalGrant без legacy ResidentAccess. Scoped staff fixtures не получают global permissions, которые скрыли бы дефект.

## Открытые продуктовые решения и границы evidence

- **P1 — требуется решение пользователя:** должен ли завершённый V2 grant окончательно подавлять действующий legacy-доступ? Текущий resolver возвращается к legacy, если активного grant нет. Эта семантика в этом candidate не меняется. Нельзя записывать универсальную гарантию «отозван весь доступ», пока правило не выбрано и не проверено.
- Перенос identity с одного User на другой и архивирование Person не используются как автоматический отзыв/передача истории. Штатная перепривязка существующей identity приглашением запрещена. Новый transfer workflow в scope не включён.
- Line self-review уже запрещён; финальная staff модерация собственного показания сохраняет текущую политику. Универсальный запрет/вторая подпись не вводятся автоматически.
- AR-06/13/14 проверяют конкуренцию закрытия обращения, redemption, water-finalize и payment-allocation. SQLite SKIP не является evidence; все эти сценарии должны пройти в PostgreSQL на точном candidate SHA. Это конечный набор гонок выпуска, без обещания проверки всех возможных комбинаций операций.
- У news attention нет read/dismiss состояния; notifications отражают текущее разрешённое состояние. Не обещать внешний email/SMS или гарантированную доставку.
- Sandbox host offline. Local SQLite проверяет логику; PostgreSQL CI — транзакции/row locks. Production-shaped staging/config/media/legacy drift и финальный live smoke пока не выполнены этим candidate.

## Запуск и сохранение evidence

1. `python backend/manage.py check`; `makemigrations --check --dry-run`; Django `water config public_site`; ops tests и script syntax checks — существующий PostgreSQL CI job.
2. Существующий browser CI дополнен `water.admin_resident_e2e_tests`. `admin-resident-contract` artifact содержит synthetic screenshots/traces обеих сторон, включая сбой.
3. Для каждой обязательной строки хранить SHA, job/test, PASS/FAIL/BLOCKED/NOT TESTED и ограничение среды. Свежий green применяется к проверенному SHA; новый drift требует только затронутых повторов.
4. Независимый reviewer сверяет requirement → literal expected result → действующий test → CI evidence. Пропущенная обязательная строка блокирует Product Done.

## Rollout / recovery

Один issue, executor, branch, PR от точного production baseline. Candidate не содержит migrations, новых schema/state values, новых внешних сервисов или секретов. Integration security work не переносится неявно.

До production: зафиксировать exact head/base/diff/CI; проверить затронутые auth/session, schema, private media и наличие legacy sources/назначений, особенно полномочия центра доступов. Подготовить backup кода/config/данных и проверенный путь code rollback. Получить конкретное разрешение на production mutation согласно global working agreement.

После установки: deployment marker соответствует candidate; services/HTTP/static/private denial; короткая приёмка обеих ролей. Merge/push не считается deploy. Нельзя заменять новую пользовательскую работу восстановлением всей БД как обычным undo. Code rollback не удаляет историю и файлы; исправление совершённых операций выполняется штатным процессом с причиной и автором.

Product Done: все обязательные строки и продуктовые решения закрыты; exact candidate CI/staging/recovery evidence подтверждены; production соответствует SHA и приёмка выполнена. До этого #121 остаётся OPEN, портфельный зачёт не добавляется.
