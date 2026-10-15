#!/bin/bash
# Claude, 15.10.2026. Полностью убирает claude-ro и его Desktop Commander.
set -Eeuo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Запусти через sudo"; exit 1; }
systemctl disable --now claude-ro-dc.service 2>/dev/null || true
rm -f /etc/systemd/system/claude-ro-dc.service
systemctl daemon-reload
pkill -u claude-ro 2>/dev/null || true
id claude-ro >/dev/null 2>&1 && userdel claude-ro 2>/dev/null || true
mountpoint -q /home/claude-ro && umount /home/claude-ro || true
sed -i '\#^/var/lib/claude-ro.img #d' /etc/fstab; systemctl daemon-reload
rm -f /var/lib/claude-ro.img; rmdir /home/claude-ro 2>/dev/null || rm -rf /home/claude-ro
df -h / | tail -1
echo "Убрано. Ещё удали устройство в https://mcp.desktopcommander.app/ (Revoke)."
