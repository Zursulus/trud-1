# Песочница (sandbox): полный анализ перед очисткой — 15.10.2026

Только чтение. **Ничего не удалено и не изменено.** Справка, не задачи.

## Итог в цифрах

- Диск 40 ГБ, занято 36 ГБ (97%), свободно 1.4 ГБ.
- Видимо без root: 32.2 ГБ. Из них **22.6 ГБ — домашняя папка `chatgpt-remote`** (269 объектов), и **≈17.4 ГБ из неё — Orchestra**.
- Системное (`/usr`, `/var`, `/boot`, `/opt`) ≈ 6 ГБ — норма.
- ≈4 ГБ недоступно без root: `/root`, PostgreSQL (в т.ч. старый кластер PG16, оставленный «для отката» после апгрейда 02.10), бэкап `/var/backups/ubuntu26-upgrade.*`, старые venv на Python 3.12.

## Что сейчас живое (не трогать)

| Что | Где |
|---|---|
| Telegram-бот (оркестратор, b1-sandbox) | `/opt/tg-bot`, `tg-bot.service` + 5 таймеров (health, git-sync, backup daily/monthly, git-clean) |
| Тестовый сайт Труд-1 | `/opt/trud-1-test`, `trud-1-test.service` (код от 16.09) |
| Desktop Commander | `~/.local/desktop-commander`, `~/.desktop-commander-device`, `~/.claude-server-commander` |
| Ночной бэкап research-vault | crontab 02:17, `~/research-vault` |
| ClamAV, PostgreSQL 18, nginx, scanner tunnel, заглушка zursulus.ru | системные службы |

Оркестратор сейчас **не запущен**: его cron (`orchestra-local-v1/worker.py`) закомментирован 06.10 с пометкой PAUSED_BY_USER. Все папки Orchestra — неактивные данные.

## Как разрастался Orchestra (по папкам и Notion)

ChatGPT вёл две записи: Notion «Orchestra v1 и v2 — карта реализации и план завершения» (08.10) и «Orchestra — Work/Codex Change Ledger» (файл `~/orchestra-local-v1/WORK_CODEX_CHANGELOG.md`, 8 записей об изменениях Codex/ChatGPT Work — это журнал внешних изменений, а не структура версий самого проекта). Отдельной «структуры версионных изменений» проекта не найдено. Восстановлено по датам:

| Даты | Этап | Папки (размер) |
|---|---|---|
| 01–03.10 | Agent Runner v1 → v1.1 → v1.2, смоук-тесты | `agent-runner*` (19 шт), `runner-v12-*`, `agent-runner-tools` 424M |
| 03.10 | Orchestra v1 (cycle3), Труд-1 #143 через агентов | `orchestra/`, `trud143/A,B,C…`, `trud-143-*` |
| 03–04.10 | runtime v2 lab, chat-v2, publisher | `orchestra-runtime-v2*`, `orchestra-chat-v2` 179M, `orchestra-publisher-*` |
| 06.10 | Method lab, dual pilot, stress, **dev-via-v1** | `orchestra-method-lab-*` (6 папок ≈1.7G), `orchestra-dual-pilot` 260M, `orchestra-v2-stress-*`, **`orchestra-v2-dev-via-v1-20261006-01` 5.4G** |
| 07.10 | Battle100, раунды r2…r16 | `orchestra-v2-battle100-r*-20261007` (15 × 14M), checkpoints, canary |
| 08.10 | v2 real-validation, full40x3, публикация на GitHub, UI 0.4.x | `orchestra-v2-real-validation` 0.5G, `orchestra-after-github-20261008-1926` 0.8G, `orchestra-ui*` |
| 09–11.10 | Запуск/восстановление CP13, frozen v1, кандидаты parallel/finish/telegram-panel | **`orchestra-launch-20261009` 4.3G**, **`orchestra-v2` 2.5G**, `task-controller-v1` 0.8G |
| 12–14.10 | «Холодный архив» (попытка разгрузить диск) | `cold-archive-reports` 99M |
| 13–15.10 | B1-контроллер, events pilot (меняются сегодня) | `orchestra-b1-controller*`, `orchestra-events-pilot-state-v1` 198M, `b1-*` |

## Что уже пытался сделать ChatGPT (11–14.10)

- Составил `EXCLUDE_FROM_CLEANUP.json` (23 защищённых пути) и `CLEANUP_CANDIDATES.json` — **список на удаление пуст**, ни одного байта не освобождено.
- Перенёс на компьютер «windas» в `D:\777\orchestra-cold-20261012` всего **23 файла / 12.8 МБ**; ещё 807 объектов (109 МБ) не смог прочитать из-за прав.
- Сами отчёты об архивации весят ~99 МБ (один `manifest.json` — 45 МБ).
- На `D:` свободно **40.9 ГБ** — места для архива хватит.

## Git внутри песочницы — что нельзя потерять

56 git-копий. Для Труд-1 проверено против GitHub: из 93 «неотправленных» коммитов **26 есть только на песочнице** (работа 30.09–06.10 по #114, #143 A/B/C, cycle3 RC, релиз #160). Лежат в `trud-143*`, `orchestra/trud-cumulative-20261003`, `orchestra/waterfix-131-20261003`, `cycle3-final-rc.tmp`, `trud161-build`, `trud-panel`, `trud-1-work`. Оркестратор: `orchestra-local-v1`, `orchestra-v2/{control,candidate,source}`, `orchestra-launch-20261009/cp13-release-checkout`, `agent-runner*` — свои локальные истории, частично не опубликованы.

## Предлагаемый план (решает владелец, по шагам)

**Шаг 0 — страховка (ничего не удаляет).** Для каждой git-копии сделать `git bundle --all` (все ветки и коммиты, десятки МБ) → скопировать на `D:\777\sandbox-git-bundles-20261015\` → сверить SHA256.

**Шаг 1 — восстановимое, ≈1.5 ГБ.** Кэши `~/.cache`, `~/.npm`, временные venv `trud-cycle3-verify.venv`, `.ci-trud-venv`, `orchestra-v2-battle-eval-env`; клоны Труд-1 `trud*-verify.tmp`, `t80v1.tmp`, `t131rel.tmp`, `trud80-build.tmp` (их коммиты есть на GitHub или в бандлах).

**Шаг 2 — архив на D: и удаление, ≈11–12 ГБ.** Упаковать в `tar.zst` с манифестом SHA256, скопировать на `D:\777\orchestra-cold-20261015\`, проверить, только потом удалить:
`orchestra-v2-dev-via-v1-20261006-01` (5.4G), `orchestra-launch-20261009` кроме `cp13-release-checkout` (≈4G), `orchestra-method-lab-*` (≈1.7G), `orchestra-v2-battle100-r*` (≈0.2G), `orchestra-dual-pilot`, `orchestra-v2-stress-*`, `orchestra-v2-real-validation`, `agent-runner*` старые RC/смоуки.

**Шаг 3 — оставить как есть.** `orchestra-v2`, `task-controller-v1`, `orchestra-local-v1`, `orchestra-b1-*`, `orchestra-events-pilot-state-v1`, `research-vault`, всё из `EXCLUDE_FROM_CLEANUP.json`, `/opt/*`.

**Шаг 4 — нужен root (решение владельца).** Старый кластер PostgreSQL 16 и бэкап апгрейда Ubuntu 26 (апгрейд прошёл 02.10, работает 2 недели) — кандидаты на удаление после отдельной проверки.

Ожидаемо: с 97% до ≈60% занятости.
