#!/bin/bash
# Read-only aggregate OTP readiness; never print account/device identifiers.
set -Eeuo pipefail
[[ $(id -u) -eq 0 ]] || { echo 'root required' >&2; exit 2; }
systemd-run --quiet --wait --pipe --collect \
    --property=User=trudsite --property=Group=trudsite \
    --property=WorkingDirectory=/opt/trud-1-site/backend \
    --property=EnvironmentFile=/etc/trud-1-site.env \
    --setenv=PGOPTIONS=-cdefault_transaction_read_only=on \
    /opt/trud-1-site/.venv/bin/python /opt/trud-1-site/backend/manage.py \
    shell --no-imports -c '
from django.contrib.auth import get_user_model
from django.db.models import Exists, OuterRef, Q
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

users = get_user_model().objects.filter(is_active=True).annotate(
    has_totp=Exists(TOTPDevice.objects.filter(user_id=OuterRef("pk"), confirmed=True)),
    has_static=Exists(StaticDevice.objects.filter(user_id=OuterRef("pk"), confirmed=True)),
)
admin_groups = ("Администратор ТСН", "Администратор СНТ")
for label, scope in (
    ("superusers", Q(is_superuser=True)),
    ("tsn_admins", Q(groups__name=admin_groups[0])),
    ("snt_admins", Q(groups__name=admin_groups[1])),
    ("all_admins", Q(is_superuser=True) | Q(groups__name__in=admin_groups)),
):
    selected = users.filter(scope).distinct()
    print(f"{label}={selected.count()}")
    print(f"{label}_without_confirmed_otp={selected.filter(has_totp=False, has_static=False).count()}")
'
