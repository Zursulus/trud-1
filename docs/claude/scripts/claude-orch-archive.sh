#!/bin/bash
# Claude, 15.10.2026. Архивация наследия Orchestra с песочницы. НИЧЕГО НЕ УДАЛЯЕТ.
# Запуск: sudo bash /home/claude-ro/claude-orch-archive.sh   (работает в фоне, прогресс: /home/zur/claude-archive/progress.log)
# Пишет архивы tar.zst + sha256 + списки файлов в /home/zur/claude-archive (владелец zur, чтобы забрать scp).
set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Запусти через sudo"; exit 1; }
if [ "${1:-}" != "--run" ]; then
  nohup bash "$0" --run >/dev/null 2>&1 &
  echo "Запущено в фоне. Прогресс: tail -f /home/zur/claude-archive/progress.log"; exit 0
fi
SRC=/home/chatgpt-remote; OUT=/home/zur/claude-archive; MINFREE_GB=3
mkdir -p $OUT; chown zur:zur $OUT; chmod 750 $OUT
LOG=$OUT/progress.log; : > $LOG; chown zur:zur $LOG
log(){ echo "$(date +%H:%M:%S) $*" | tee -a $LOG; }
cd $SRC || exit 1
shopt -s nullglob
declare -A G
G[01-dev-via-v1]="orchestra-v2-dev-via-v1-20261006-01"
G[02-launch-20261009]="orchestra-launch-20261009"
G[03-orchestra-v2]="orchestra-v2"
REST=(); for d in orchestra* task-controller* autonomy-* method_* b1-* B1* reminder* project-stop*; do
  case "$d" in orchestra-v2-dev-via-v1-20261006-01|orchestra-launch-20261009|orchestra-v2) continue;; esac; REST+=("$d"); done
G[04-orchestra-rest]="${REST[*]}"
G[05-agent-runner]="$(echo agent-runner* runner-*)"
G[06-trud-copies]="$(echo trud* t80v1.tmp t131rel.tmp vtb74-verify.tmp cycle3-final-rc.tmp .ci-trud-venv)"
for k in $(printf '%s\n' "${!G[@]}" | sort); do
  items=${G[$k]}; [ -n "$items" ] || { log "$k: пусто, пропуск"; continue; }
  free=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)
  [ "$free" -ge $MINFREE_GB ] || { log "СТОП перед $k: свободно ${free}G < ${MINFREE_GB}G. Забери готовые архивы и сообщи Claude."; exit 1; }
  log "$k: старт ($(du -sch $items 2>/dev/null | tail -1 | cut -f1)), свободно ${free}G"
  tar --exclude='__pycache__' -cf - $items 2>>$OUT/$k.tar-errors.log | zstd -T0 -3 -q -o $OUT/$k.tar.zst
  if zstd -tq $OUT/$k.tar.zst; then
    zstd -dc $OUT/$k.tar.zst | tar -tf - > $OUT/$k.files.txt
    (cd $OUT && sha256sum $k.tar.zst > $k.sha256)
    echo "$items" | tr ' ' '\n' > $OUT/$k.dirs.txt
    log "$k: ГОТОВО $(du -h $OUT/$k.tar.zst | cut -f1), файлов $(wc -l < $OUT/$k.files.txt)"
  else log "$k: ОШИБКА проверки архива"; fi
  chown zur:zur $OUT/$k.*; chmod 640 $OUT/$k.*
done
log "ВСЁ. Свободно $(df -h --output=avail / | tail -1). Ничего не удалено."
