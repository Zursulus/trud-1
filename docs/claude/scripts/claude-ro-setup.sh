#!/bin/bash
# Claude, 15.10.2026. Создаёт учётку claude-ro: читает ЛЮБЫЕ файлы, писать может только в /home/claude-ro.
# Запуск: sudo bash claude-ro-setup.sh            — шаг 1 (установка) + шаг 2 (вход в Desktop Commander)
# Отмена: sudo bash claude-ro-remove.sh
set -Eeuo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Запусти через sudo"; exit 1; }
U=claude-ro; H=/home/$U; DC=$H/.local/desktop-commander
NODE=$(command -v node || true); [ -x "${NODE:-}" ] || NODE=/opt/node/current/bin/node
[ -x "$NODE" ] || { echo "Не найден node"; exit 1; }
NPM="$(dirname "$NODE")/npm"; [ -x "$NPM" ] || { echo "Не найден npm рядом с $NODE"; exit 1; }
FREE=$(df -Pk /home | awk 'NR==2{print $4}'); [ "$FREE" -gt 300000 ] || { echo "Меньше 300 МБ свободно в /home — сначала расширь диск"; exit 1; }

echo "== 1/4 учётка $U (без пароля, без входа, без sudo)"
id $U >/dev/null 2>&1 || useradd --system --create-home --home-dir $H --shell /usr/sbin/nologin $U
passwd -l $U >/dev/null
for g in sudo wheel adm; do gpasswd -d $U $g >/dev/null 2>&1 || true; done

echo "== 2/4 Desktop Commander в $DC"
sudo -u $U -H env PATH="$(dirname "$NODE"):/usr/bin:/bin" "$NPM" install --silent --prefix "$DC" @wonderwhy-er/desktop-commander@latest
JS="$DC/node_modules/@wonderwhy-er/desktop-commander/dist/index.js"; [ -f "$JS" ] || { echo "DC не установился"; exit 1; }

echo "== 3/4 служба claude-ro-dc.service (читать всё, писать только в $H и /tmp)"
cat > /etc/systemd/system/claude-ro-dc.service <<UNIT
[Unit]
Description=Desktop Commander for Claude (read-only everywhere)
After=network-online.target
Wants=network-online.target
[Service]
User=$U
Group=$U
Environment=HOME=$H
Environment=PATH=$(dirname "$NODE"):/usr/local/bin:/usr/bin:/bin
WorkingDirectory=$H
ExecStart=$NODE $JS remote --disable-no-sleep
Restart=always
RestartSec=10
# Право читать любые файлы, и больше никаких
AmbientCapabilities=CAP_DAC_READ_SEARCH
CapabilityBoundingSet=CAP_DAC_READ_SEARCH
NoNewPrivileges=yes
# Вся система только для чтения, кроме своей папки
ProtectSystem=strict
ReadWritePaths=$H
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload

echo "== 4/4 вход в аккаунт Desktop Commander (один раз)"
echo "Сейчас появится ссылка — открой её в браузере и войди в тот же аккаунт Desktop Commander."
echo "Когда увидишь, что устройство подключено, нажми Ctrl+C — дальше служба запустится сама."
trap 'echo; echo "Ctrl+C принят, продолжаю установку службы..."' INT
sudo -u $U -H env PATH="$(dirname "$NODE"):/usr/bin:/bin" "$NODE" "$JS" remote --disable-no-sleep || true
trap - INT
systemctl enable --now claude-ro-dc.service
sleep 3; systemctl --no-pager --lines=5 status claude-ro-dc.service || true
echo "ГОТОВО. Проверка: sudo -u $U touch /etc/claude-test  -> должно быть 'Read-only file system' или 'Permission denied'"
