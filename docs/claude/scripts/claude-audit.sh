#!/bin/bash
# Claude, 15.10.2026. Полный аудит сервера ТОЛЬКО НА ЧТЕНИЕ (запускается от claude-ro).
# НЕ читает: содержимое .env/ключей/секретов, базы данных, бэкапы, реестры жителей. Только наличие, размеры, права.
OUT=${1:-/home/claude-ro/audit-$(hostname)-$(date +%Y%m%d-%H%M).txt}
exec > "$OUT" 2>&1
s(){ echo; echo "################ $*"; }
s "0. ХОСТ"; date -Is; hostname -f; . /etc/os-release; echo "$PRETTY_NAME / $(uname -r)"; uptime
nproc; free -h; df -hT -x tmpfs -x devtmpfs -x overlay; lsblk -o NAME,SIZE,TYPE,MOUNTPOINT 2>/dev/null
s "1. ПАКЕТЫ"; dpkg -l | grep -c '^ii'; apt list --upgradable 2>/dev/null | tail -n +2 | wc -l; dpkg -l 'linux-image-*' | awk '/^ii/{print $2}'
for b in python3 node npm git psql nginx docker restic; do printf "%-8s %s\n" $b "$($b --version 2>/dev/null | head -1)"; done
ls -d /opt/node/* /usr/lib/python3* 2>/dev/null
s "2. УЧЁТКИ"; awk -F: '$3>=1000 || $7 !~ /(nologin|false|sync)$/ {print $1, $3, $6, $7}' /etc/passwd
echo "-- группы с людьми"; getent group sudo adm docker systemd-journal www-data postgres 2>/dev/null
echo "-- sudoers.d (содержимое правил)"; for f in /etc/sudoers.d/*; do echo "## $f"; grep -v '^\s*#' "$f" | grep -v '^\s*$'; done
echo "-- authorized_keys: количество и отпечатки (не сами ключи)"
for f in /root/.ssh/authorized_keys /home/*/.ssh/authorized_keys; do [ -f "$f" ] && { echo "## $f"; ssh-keygen -lf "$f" 2>/dev/null; }; done
echo "-- sshd"; sshd -T 2>/dev/null | grep -Ei '^(port|permitrootlogin|passwordauthentication|pubkeyauthentication|allowusers|allowgroups) ' || grep -Ei '^\s*(Port|PermitRootLogin|PasswordAuthentication|AllowUsers)' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/* 2>/dev/null
echo "-- последние входы"; last -n 25 -w 2>/dev/null | head -27
s "3. СЛУЖБЫ"; systemctl list-units --type=service --state=running --no-legend --plain --no-pager | awk '{print $1}'
echo "-- упавшие"; systemctl --failed --no-legend --plain --no-pager
echo "-- свои юниты (/etc/systemd/system)"; for u in /etc/systemd/system/*.service /etc/systemd/system/*.timer /etc/systemd/system/*.path /etc/systemd/system/*.socket; do [ -f "$u" ] || continue; echo "## $(basename $u) [$(systemctl is-enabled $(basename $u) 2>/dev/null)/$(systemctl is-active $(basename $u) 2>/dev/null)]"; grep -E '^(Description|User|Group|WorkingDirectory|ExecStart|ExecStartPre|OnCalendar|OnUnitActiveSec|PathChanged|PathModified|Unit|EnvironmentFile)=' "$u"; done
echo "-- drop-in"; ls -d /etc/systemd/system/*.d 2>/dev/null
echo "-- таймеры"; systemctl list-timers --all --no-legend --plain --no-pager
echo "-- cron"; ls -l /etc/cron.d 2>/dev/null; for f in /etc/crontab /etc/cron.d/*; do echo "## $f"; grep -v '^\s*#' "$f" 2>/dev/null | grep -v '^\s*$'; done
for f in /var/spool/cron/crontabs/*; do echo "## crontab $(basename $f)"; grep -v '^\s*#' "$f" | grep -v '^\s*$'; done 2>/dev/null
s "4. СЕТЬ"; ip -br a; ss -tlnpH 2>/dev/null; ss -ulnpH 2>/dev/null | head -20
echo "-- firewall"; nft list ruleset 2>/dev/null | head -60; ufw status 2>/dev/null; ls /etc/wireguard 2>/dev/null
echo "-- nginx сайты"; for f in /etc/nginx/sites-enabled/*; do echo "## $f -> $(readlink -f $f)"; grep -Ev '^\s*#|^\s*$' "$f" | grep -E 'server_name|listen|root|proxy_pass|alias|include|ssl_certificate |return|location' ; done
echo "-- сертификаты"; ls /etc/letsencrypt/live 2>/dev/null; for c in /etc/letsencrypt/live/*/cert.pem; do [ -f "$c" ] && echo "$c $(openssl x509 -enddate -noout -in $c)"; done
s "5. ПРИЛОЖЕНИЯ"; for d in /opt/* /srv/* /var/www/*; do [ -e "$d" ] && printf "%8s  %s  %s\n" "$(du -sh "$d" 2>/dev/null | cut -f1)" "$(stat -c '%U:%G %y' "$d" | cut -c1-30)" "$d"; done
echo "-- git-копии везде"
find / -xdev \( -path /proc -o -path /sys -o -path /var/lib/postgresql \) -prune -o -name .git -type d -print 2>/dev/null | while read -r g; do
  r=${g%/.git}; G="git -c safe.directory=* -C $r"
  url=$($G remote get-url origin 2>/dev/null | sed -E 's#https://[^@/]*@#https://#')
  printf "%s | %s | %s | %s | dirty=%s | branches=%s | unpushed=%s | last=%s\n" "$r" "${url:-нет remote}" "$($G rev-parse --abbrev-ref HEAD 2>/dev/null)" "$($G rev-parse --short HEAD 2>/dev/null)" "$($G status --porcelain 2>/dev/null | wc -l)" "$($G for-each-ref refs/heads 2>/dev/null | wc -l)" "$($G log --branches --not --remotes --oneline 2>/dev/null | wc -l)" "$($G log -1 --format=%ad --date=short 2>/dev/null)"
done
s "6. БЭКАПЫ (только размеры и даты)"; for d in /var/backups/* /root/*backup* /home/*/backups /home/*/*backup* /srv/backup*; do [ -e "$d" ] && printf "%8s  %s  %s\n" "$(du -sh "$d" 2>/dev/null | cut -f1)" "$(stat -c %y "$d" | cut -c1-16)" "$d"; done
ls /etc/restic* /root/.config/restic 2>/dev/null; echo "-- PostgreSQL: размер каталога данных"; du -sh /var/lib/postgresql/*/* 2>/dev/null
s "7. ДИСК: крупное (≥50 МБ, 3 уровня)"; du -x -d 3 / 2>/dev/null | awk '$1>=51200' | sort -rn | head -60 | awk '{printf "%7.0fM %s\n",$1/1024,$2}'
s "8. СЕКРЕТЫ: только пути и права (содержимое НЕ читается)"
find / -xdev \( -path /proc -o -path /sys -o -path /var/lib/postgresql -o -path '*/node_modules' -o -path '*/.git' \) -prune -o -type f \( -name '.env' -o -name '*.env' -o -name '*.pem' -o -name 'id_*' -o -name '*.key' -o -name 'credentials*' -o -name '*token*' -o -name '*secret*' \) -printf '%m %u:%g %10s %TY-%Tm-%Td %p\n' 2>/dev/null | grep -v '/proc/' | head -80
s "9. ОШИБКИ В ЖУРНАЛЕ (7 дней, по юнитам)"; journalctl --since "-7d" -p err -o json --no-pager 2>/dev/null | grep -o '"_SYSTEMD_UNIT":"[^"]*"' | sort | uniq -c | sort -rn | head -20
journalctl -b -p warning --no-pager -o short | tail -25
s "КОНЕЦ"; date -Is
