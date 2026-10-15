#!/bin/bash
# Claude, 15.10.2026. Создаёт учётку claude-ro: читает ЛЮБЫЕ файлы, писать может только в /home/claude-ro.
# Всё ставится в отдельный файл-диск 600 МБ (/var/lib/claude-ro.img): переполнить основной диск невозможно.
# Отмена: sudo bash claude-ro-remove.sh
set -Eeuo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Запусти через sudo"; exit 1; }
U=claude-ro; H=/home/$U; DC=$H/.local/desktop-commander
NODE=/opt/node/current/bin/node; [ -x "$NODE" ] || NODE=$(command -v node || true)   # нужен Node 22+ (в /usr/bin на проде Node 20)
"$NODE" -e 'process.exit(typeof WebSocket==="function"?0:1)' || { echo "Нужен Node 22+, а $NODE — $("$NODE" -v)"; exit 1; }
[ -x "$NODE" ] || { echo "Не найден node"; exit 1; }
NPM="$(dirname "$NODE")/npm"; [ -x "$NPM" ] || { echo "Не найден npm рядом с $NODE"; exit 1; }
CAP=600   # МБ: потолок для всего, что поставит claude-ro (отдельный файл-диск)
IMG=/var/lib/claude-ro.img
FREE=$(df -Pm / | awk 'NR==2{print $4}'); mountpoint -q $H || [ "$FREE" -gt $((CAP+400)) ] || { echo "Нужно хотя бы $((CAP+400)) МБ свободно, а есть $FREE"; exit 1; }
# Не скачивать браузер Chrome/Chromium (в первый раз он занял весь остаток диска песочницы)
NOBROWSER="TMPDIR=/home/claude-ro/tmp npm_config_cache=/home/claude-ro/.npm PUPPETEER_SKIP_DOWNLOAD=true PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1"
guard() { local f; f=$(df -Pk / | awk 'NR==2{print $4}'); [ "$f" -gt 300000 ] || { echo "СТОП: на основном диске меньше 300 МБ после шага '$1'. Откати: sudo bash $(dirname "$0")/claude-ro-remove.sh"; systemctl stop claude-ro-dc.service 2>/dev/null || true; exit 1; }; }

echo "== 1/4 учётка $U (без пароля, без входа, без sudo)"
if ! mountpoint -q $H; then
  [ -e $IMG ] || { fallocate -l ${CAP}M $IMG; chmod 600 $IMG; mkfs.ext4 -q -m 0 $IMG; }
  mkdir -p $H; grep -q "^$IMG " /etc/fstab || echo "$IMG $H ext4 loop,nodev,nosuid 0 2" >> /etc/fstab
  systemctl daemon-reload; mount $H
fi
id $U >/dev/null 2>&1 || useradd --system --no-create-home --home-dir $H --shell /usr/sbin/nologin $U
chown $U:$U $H; chmod 750 $H; mkdir -p $H/tmp; chown $U:$U $H/tmp
echo "   папка $H = отдельный диск ${CAP} МБ: $(df -h $H | awk 'NR==2{print $2}')"
passwd -l $U >/dev/null
for g in sudo wheel adm; do gpasswd -d $U $g >/dev/null 2>&1 || true; done

echo "== 2/4 Desktop Commander в $DC"
sudo -u $U -H env $NOBROWSER PATH="$(dirname "$NODE"):/usr/bin:/bin" "$NPM" install --silent --prefix "$DC" @wonderwhy-er/desktop-commander@latest
JS="$DC/node_modules/@wonderwhy-er/desktop-commander/dist/index.js"; [ -f "$JS" ] || { echo "DC не установился"; exit 1; }
guard "установка"

echo "== 3/4 служба claude-ro-dc.service (читать всё, писать только в $H и /tmp)"
cat > /etc/systemd/system/claude-ro-dc.service <<UNIT
[Unit]
Description=Desktop Commander for Claude (read-only everywhere)
After=network-online.target
Wants=network-online.target
RequiresMountsFor=$H
[Service]
User=$U
Group=$U
Environment=HOME=$H
Environment=PATH=$(dirname "$NODE"):/usr/local/bin:/usr/bin:/bin
Environment=TMPDIR=$H/tmp PUPPETEER_SKIP_DOWNLOAD=true PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
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
TemporaryFileSystem=/tmp:size=50M
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload

echo "== 4/4 запуск службы и ссылка для входа"
systemctl enable claude-ro-dc.service; systemctl restart claude-ro-dc.service
sleep 3; systemctl --no-pager --lines=5 status claude-ro-dc.service || true
LINKFILE=/tmp/claude-ro-login-link.txt; LINK=""
for i in $(seq 1 30); do
  LINK=$(journalctl -u claude-ro-dc.service --since "-5min" -o cat 2>/dev/null | grep -o 'https://mcp.desktopcommander.app/device/verify?user_code=[A-Z0-9-]*' | tail -1 || true)
  [ -n "$LINK" ] && break; sleep 2
done
if [ -n "$LINK" ]; then
  echo "$LINK" > "$LINKFILE"; chmod 644 "$LINKFILE"
  echo; echo "ССЫЛКА ДЛЯ ВХОДА (Claude пришлёт её в чат кликабельной): $LINK"
  echo "Код действует 15 минут. Терминал можно не трогать."
else
  echo "Ссылку в журнале не нашёл. Посмотреть: sudo journalctl -u claude-ro-dc -n 30"
fi
echo "Жду 30 секунд и проверяю, что служба не съела диск..."; sleep 30; guard "запуск службы"
df -h /home | tail -1
echo "ГОТОВО. Проверка: sudo -u $U touch /etc/claude-test  -> должно быть 'Read-only file system' или 'Permission denied'"
