# Серверы и устройства — живой снимок 15.10.2026 ~14:05 CEST

Снято Claude через Desktop Commander, **только чтение**. Ничего не менялось. Справка, не задачи.

## Как Claude и ChatGPT попадают на машины

Desktop Commander Remote (аккаунт владельца, https://mcp.desktopcommander.app/). Устройства:

| Устройство | Что это | Под кем работает DC |
|---|---|---|
| `980121.vds.as210546.net` | боевой сервер | `chatgpt-remote` (uid 1002) |
| `sandbox` | тестовый сервер | `chatgpt-remote` |
| `WIN-AJGR4H3DREP` | Windows-ПК «windas» (с ним связан чат Claude) | — |
| `DESKTOP-GT3LU58` | ноутбук (пользователь `Olga MacBook`), автозапуск DC через `Startup\desktop-commander.cmd` | — |

Служба `chatgpt-remote.service` на обоих серверах — это и есть агент Desktop Commander. Тем же каналом пользуются ChatGPT и Claude.

## 980121 — production

- Debian 13 (trixie), ядро 6.12, аптайм 9 дней. **RAM 1 ГБ**, **диск 9.9 ГБ занят на 89% (свободно 1.1 ГБ)**.
- Сайт: `trud-1-site.service`, nginx, PostgreSQL 17. Marker: коммит `6fb32ef`, deployed_at 2026-10-06 09:07 UTC — совпадает с Notion.
- `/opt/trud-1-site` — не git-checkout (релиз выкладывается копией).
- Бэкапы: `trud-1-backup.timer` (02:30) и `trud-backup.timer` (04:00) — сегодня оба **success**.
- Боты: `tg-bot-mobile-ui-test.service` (Life OS) работает; `/opt/tg-bot-server-recovery` лежит, служба не запущена.
- Пользователи с shell: root, zur, postgres, **mikemo**, chatgpt-remote.
- sudoers.d (выданы ChatGPT): `chatgpt-trud-diag`, `lifeos-chatgpt-deploy`, `trud-chatgpt-deploy`, `trud-deploy`, `trud-readonly-chatgpt`.
- У `chatgpt-remote` 1 SSH-ключ. В домашней папке (~800 МБ) — десятки deploy-скриптов ChatGPT (`deploy-trud-*.sh`, 26.09–02.10) и `add-bitvise-key-*.py`.
- Упавшие юниты: 3 × `trud-diag@…` (разовые диагностические запросы ChatGPT, не влияют на сайт).
- Последний вход по SSH: `chatgpt-remote` **сегодня 11:34 CEST с 91.108.44.200**.
- Часовой пояс сервера CEST.

## sandbox — тест

- Ubuntu 26.04.1, ядро 7.0, перезагружен ~2.5 ч назад. RAM 3.3 ГБ. **Диск 40 ГБ занят на 97% (свободно 1.4 ГБ)** — критично.
- Работают: `tg-bot.service` (оркестратор), `trud-1-test.service`, PostgreSQL 18, ClamAV, `trud-scanner-tunnel`, nginx.
- `trud-1-test` стоит на `feature/water-admin` @ `7177231` от **16.09** — на месяц старее прода.
- Упали после загрузки: `system-maintenance-postboot.service` (проверка sandbox после перезагрузки, exit 1) и `grub2-common.service`.
- Пользователи с shell: root, zur, postgres, chatgpt-remote, **agent-runner**.

## На что обратить внимание (решает владелец)

1. Место на дисках: sandbox 97%, прод 89% при 1 ГБ RAM.
2. Кто такой `mikemo` на проде и `agent-runner` на sandbox.
3. Вход `chatgpt-remote` на прод сегодня в 11:34 при действующем GLOBAL STOP — проверить, кто это был.
4. Пять sudo-правил для ChatGPT остаются активными.
5. Тестовый стенд сильно отстал от прода — как staging он сейчас не годится.
6. Незакрытые с 19.09 задачи по утёкшим секретам (Linear ZUR-18/19/20).
