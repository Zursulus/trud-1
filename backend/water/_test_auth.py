"""Authenticated fixtures for tests of permissions and business workflows."""
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from .management.commands.setup_roles import ADMIN, LEGACY_ADMIN


def login_with_admin_otp(client, user):
    """Verify admin fixtures; keep other users' sessions password-only."""
    client.force_login(user)
    verify_admin_otp(client, user)


def verify_admin_otp(client, user):
    """Attach verified OTP state after a fixture has authenticated."""
    if user.is_superuser or user.groups.filter(name__in=(ADMIN, LEGACY_ADMIN)).exists():
        device, _ = TOTPDevice.objects.get_or_create(user=user, name='default', confirmed=True)
        session = client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()
