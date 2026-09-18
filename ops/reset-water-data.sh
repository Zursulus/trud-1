#!/bin/bash
# Destructive maintenance command. Run as root only after explicit approval:
#   bash ops/reset-water-data.sh RESET-TEST-WATER-DATA
set -Eeuo pipefail
umask 077
export SYSTEMD_PAGER=cat

TOKEN=${1:-}
[ "$TOKEN" = "RESET-TEST-WATER-DATA" ] || {
  echo 'ОТКАЗ: требуется точная фраза RESET-TEST-WATER-DATA'; exit 2;
}
[ "$(id -u)" -eq 0 ] || { echo 'ОТКАЗ: запускать только от root'; exit 2; }

APP=/opt/trud-1-site
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP=/var/backups/trud-before-water-reset-$STAMP
mkdir -m 700 "$BACKUP"

runuser -u trudsite -- git -C "$APP" rev-parse HEAD > "$BACKUP/commit.txt"
runuser -u postgres -- pg_dump -Fc trud_site > "$BACKUP/trud_site.dump"
pg_restore --list "$BACKUP/trud_site.dump" > "$BACKUP/restore-list.txt"
test -s "$BACKUP/trud_site.dump"
test -s "$BACKUP/restore-list.txt"
sha256sum "$BACKUP/trud_site.dump" "$BACKUP/commit.txt" > "$BACKUP/SHA256SUMS"

echo "Бэкап проверен: $BACKUP"
echo "Размер: $(du -h "$BACKUP/trud_site.dump" | cut -f1)"
echo 'Очищаем только прикладные данные water; пользователей, роли, права и настройки Django не трогаем.'

cd "$APP/backend"
systemd-run --quiet --wait --pipe --collect \
  --property=User=trudsite --property=Group=trudsite \
  --property=WorkingDirectory="$APP/backend" \
  --property=EnvironmentFile=/etc/trud-1-site.env \
  "$APP/.venv/bin/python" manage.py shell <<'PY'
from django.db import transaction
from water.models import (
    GroupConsumption, Reading, Meter, Membership, PlotRelation,
    LandPlot, Person, WaterGroup, SupplyNode, Account,
)

# Order is deliberate: dependants first. A single transaction means all-or-nothing.
models = [
    GroupConsumption, Reading, Meter, Membership, PlotRelation,
    LandPlot, Person, WaterGroup, SupplyNode, Account,
]
with transaction.atomic():
    before = {m.__name__: m.objects.count() for m in models}
    for model in models:
        model.objects.all().delete()
    after = {m.__name__: m.objects.count() for m in models}
    if any(after.values()):
        raise RuntimeError(f'После очистки остались записи: {after}')
print('До очистки:', before)
print('После очистки:', after)
PY

echo 'ГОТОВО. Прикладные данные water очищены.'
echo "ВОССТАНОВЛЕНИЕ при необходимости: $BACKUP/trud_site.dump"
