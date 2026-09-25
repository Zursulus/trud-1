#!/bin/bash
set -Eeuo pipefail
umask 077

render() {
cat <<'EOF'
[Journal]
SystemMaxUse=512M
SystemKeepFree=1G
MaxRetentionSec=30day
EOF
}

if [ "${1:-}" = "--render" ]; then
    render
    exit 0
fi

test "$(id -u)" -eq 0
DIR=/etc/systemd/journald.conf.d
FILE="$DIR/trud-1-retention.conf"
BACKUP=$(mktemp -d /var/backups/trud-zur120-journald.XXXXXXXX)
install -d -m 755 "$DIR"
EXISTED=0
if [ -f "$FILE" ]; then
    cp -a "$FILE" "$BACKUP/trud-1-retention.conf.before"
    EXISTED=1
fi

rollback() {
    local rc=$?
    trap - EXIT INT TERM
    [ "$rc" -ne 0 ] || return 0
    set +e
    if [ "$EXISTED" = 1 ]; then
        cp -a "$BACKUP/trud-1-retention.conf.before" "$FILE"
    else
        rm -f "$FILE"
    fi
    systemctl restart systemd-journald
    echo "Изменение journald не завершено. Копия: $BACKUP" >&2
    exit "$rc"
}
trap rollback EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

TMP=$(mktemp "$DIR/.trud-1-retention.XXXXXXXX")
render > "$TMP"
chmod 644 "$TMP"
chown root:root "$TMP"
mv -f "$TMP" "$FILE"

systemd-analyze cat-config systemd/journald.conf >/dev/null
systemctl restart systemd-journald
systemctl is-active --quiet systemd-journald

grep -Fx 'SystemMaxUse=512M' "$FILE" >/dev/null
grep -Fx 'SystemKeepFree=1G' "$FILE" >/dev/null
grep -Fx 'MaxRetentionSec=30day' "$FILE" >/dev/null

trap - EXIT INT TERM
echo "Journald retention policy установлена. Копия: $BACKUP"