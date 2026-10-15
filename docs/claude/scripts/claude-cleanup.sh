#!/bin/bash
# Claude, 15.10.2026. Чистка ТОЛЬКО кэшей и временного мусора. Рабочие файлы, базы, бэкапы релизов не трогает.
# Сначала показывает список с размерами и спрашивает "y". Запуск: sudo bash /tmp/claude-cleanup.sh
set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Запусти через sudo"; exit 1; }
CR=/home/chatgpt-remote
ITEMS=(
  "$CR/.cache/puccinialin"                         # Rust-тулчейн, скачанный pip при сборке (кэш)
  "$CR/.claude-server-commander/puppeteer-cache"   # Chrome, скачанный Desktop Commander
  "$CR/.cache/pip" "$CR/.cache/uv"                 # кэши pip/uv
  /home/zur/.npm/_cacache /home/zur/.cache/pip      # кэши npm/pip у zur
  /root/.npm/_cacache /root/.cache/pip              # кэши npm/pip у root
  /home/claude-ro/.npm/_cacache                     # мой npm-кэш (внутри моего диска 600 МБ)
)
echo "== Будет удалено:"
TOTAL=0
for p in "${ITEMS[@]}"; do [ -e "$p" ] || continue; s=$(du -sm "$p" | cut -f1); TOTAL=$((TOTAL+s)); printf "%6s МБ  %s\n" "$s" "$p"; done
OLDLOGS=$(ls -1t $CR/.claude-server-commander/claude_tool_call_*.log 2>/dev/null | tail -n +3)
[ -n "$OLDLOGS" ] && { s=$(du -cm $OLDLOGS | tail -1 | cut -f1); TOTAL=$((TOTAL+s)); printf "%6s МБ  старые логи Desktop Commander (%s шт., 2 последних остаются)\n" "$s" "$(echo "$OLDLOGS" | wc -l)"; }
echo "   + apt clean (кэш пакетов): $(du -sm /var/cache/apt | cut -f1) МБ"
echo "   + журнал systemd ужать до 100 МБ (сейчас $(journalctl --disk-usage | grep -o '[0-9.]*[MG]'))"
echo "   + старые ядра (apt autoremove), текущее $(uname -r) остаётся:"
apt-get -s autoremove 2>/dev/null | awk '/^Remv/{print "       "$2}'
echo "Итого кэшей ≈ ${TOTAL} МБ + apt/журнал/ядра. Свободно сейчас: $(df -h / | awk 'NR==2{print $4}')"
read -r -p "Удалить? (y/N) " a; [ "$a" = "y" ] || { echo "Отменено, ничего не удалено."; exit 0; }

for p in "${ITEMS[@]}"; do [ -e "$p" ] && rm -rf --one-file-system "$p" && echo "удалено: $p"; done
[ -n "$OLDLOGS" ] && rm -f $OLDLOGS && echo "удалены старые логи Desktop Commander"
apt-get clean && echo "apt clean: готово"
journalctl --vacuum-size=100M >/dev/null 2>&1 && echo "журнал: ужат до 100 МБ"
DEBIAN_FRONTEND=noninteractive apt-get -y autoremove --purge >/dev/null 2>&1 && echo "старые пакеты/ядра: удалены"
echo "Свободно стало: $(df -h / | awk 'NR==2{print $4}')"
