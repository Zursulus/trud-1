# Аудит прода 980121 — 15.10.2026 (после чистки и перезагрузки)

Только чтение через `claude-ro`. Данные жителей, базы, бэкапы и содержимое секретов не открывались. Справка, не задачи.

## Система
- Debian 13 (trixie), ядро 6.12.111; 1 vCPU, **RAM 967 МБ**, swap 512 МБ (занят 129 МБ сразу после старта).
- Диск 10 ГБ: занято 7.2 ГБ, **свободно 2.2 ГБ** (78%). Отдельно — мой файл-диск 600 МБ под `/home/claude-ro`.
- Python 3.13.5, Node 22.23.3 (в `/opt/node`; системный `/usr/bin/node` — Node 20), PostgreSQL 17.11. Обновлений к установке: 3.
- Сертификат trud-1.ru (Let's Encrypt) до **12.12.2026**, certbot.timer продлевает.

## Учётки и доступ
| Учётка | Что это |
|---|---|
| `zur` | владелец, sudo |
| `mikemo` | заказчик Труд-1, **в группе sudo** |
| `chatgpt-remote` | агент ChatGPT (Desktop Commander), без sudo, кроме `trud-db-ro` |
| `claude-ro` | Claude, только чтение (CAP_DAC_READ_SEARCH, ProtectSystem=strict) |
| `trudsite` | от неё работает сайт |
| `postgres` | база |

sudo-правила в `/etc/sudoers.d/` (без пароля):
- `chatgpt-remote` → `/usr/local/sbin/trud-db-ro` (диагностика БД только на чтение);
- `zur` → `trud-deploy-approved ""`, `trud-deploy-chatgpt *`, `trud-readonly-chatgpt *`, `lifeos-deploy-chatgpt *` — обёртки для деплоя, которыми пользовался ChatGPT.

SSH-ключи у `zur` (6): с `WIN-AJGR4H3DREP`, ноутбука `Olga-MacBook-DESKTOP-GT3LU58`, `pc-zur`, Termius, `chatgpt-remote-trud-1-2026-09-24`, `root@980121`. У `chatgpt-remote` — 1 ключ `trud-scanner-tunnel` (обратный туннель ClamAV с песочницы).
SSH: в `sshd_config.d` одновременно `00-local-key-only.conf` (PasswordAuthentication no) и `99-password-login.conf` (yes). В sshd побеждает первое значение, то есть вход по паролю, скорее всего, выключен — проверить командой `sudo sshd -T | grep passwordauth`. За 7 дней 203 ошибки в журнале ssh — это обычный перебор паролей из интернета.
Файрвол: правил не видно (nft/ufw пустые или нет прав прочитать) — проверить отдельно.

## Службы
Работают: `trud-1-site` (gunicorn на 127.0.0.1:8002, Python 3.13), nginx (80/443), PostgreSQL 17 (127.0.0.1:5432), `tg-bot-mobile-ui-test` (Life OS, от `zur`), `chatgpt-remote`, `claude-ro-dc`, exim4 (почта, только localhost), unattended-upgrades.
Не запущены, но установлены: `tg-bot-server-recovery` (disabled), брокеры `lifeos-release` и `system-maintenance` (запускаются по файлу-запросу), `trud-diag@` (диагностика по сокету).
Таймеры: `trud-1-backup` 03:20 МСК, `trud-backup` 02:00 UTC — сегодня оба успешно. Упавших служб после перезагрузки нет.

## Сайт
- nginx: `/admin/`, `/work/` → Django (8002); статика админки из `/opt/trud-1-site/backend/staticfiles/`.
- **Главная страница сайта — статический релиз `/var/www/trud-1/releases/bef1cb7`** (коммит от 13.09 из `main`).
- Код приложения: `/opt/trud-1-site` (258 МБ), коммит `6fb32ef`, не git-checkout.

## Место (крупное)
`/home/zur` 1.1 ГБ (в т.ч. `.nvm` 565 МБ, `.codex` 532 МБ — Codex запускался под `zur`), `/home/chatgpt-remote` 0.8 ГБ (из них **Chrome для Desktop Commander 399 МБ — скачивается заново при каждом запуске**), `/opt` 0.7 ГБ, `/var/backups` 212 МБ, `/var/log` 194 МБ.
Остатки после обновления Debian 12→13 и Python 3.11→3.13 (02.10): `.venv.py311-*` у сайта (109 МБ) и ботов (~150 МБ), кластер PostgreSQL 15 (87 МБ) рядом с 17-м, ядро 6.1.0-53.

## Бэкапы
- Ежедневный `trud-1-daily` (104 МБ) + шифрованный `trud-backup`.
- В `/var/backups` ~150 одноразовых снимков ChatGPT перед каждым деплоем (17.09–06.10), часть — дампы БД (`*.dump`). Политики хранения нет. **Содержат данные жителей** — чистить только по решению владельца.

## Что решить владельцу
1. `mikemo` (заказчик) имеет sudo на проде — так и задумано?
2. Проверить вход по паролю в SSH и файрвол.
3. Chrome от Desktop Commander у ChatGPT (399 МБ) возвращается после каждой перезагрузки — отключить так же, как у `claude-ro`?
4. Убрать остатки Python 3.11 / PostgreSQL 15 / старого ядра (~400 МБ) — после проверки, что откат на них уже не нужен.
5. Политика хранения одноразовых бэкапов в `/var/backups`.
