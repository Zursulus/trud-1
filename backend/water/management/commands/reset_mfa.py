"""Remove a staff member's MFA devices after verified identity recovery."""

from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from water.models import User


class Command(BaseCommand):
    help = (
        'Disable MFA for one user and revoke their active sessions. '
        'Use only after verifying the employee identity; the next admin login '
        'will require setting up a new authenticator and backup codes.'
    )

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('--yes', action='store_true', help='Confirm the irreversible reset.')

    def handle(self, *args, **options):
        if not options['yes']:
            raise CommandError('Add --yes after verifying the employee identity.')
        try:
            user = User.objects.get(username=options['username'])
        except User.DoesNotExist as exc:
            raise CommandError('User not found.') from exc

        totp_count, _ = TOTPDevice.objects.filter(user=user).delete()
        static_count, _ = StaticDevice.objects.filter(user=user).delete()
        revoked = 0
        for session in Session.objects.all().iterator():
            try:
                data = session.get_decoded()
            except Exception:
                # Expired or malformed sessions are left to Django's normal cleanup.
                continue
            if data.get('_auth_user_id') == str(user.pk):
                session.delete()
                revoked += 1

        self.stdout.write(self.style.SUCCESS(
            f'MFA reset for {user.username}: TOTP devices {totp_count}, '
            f'recovery devices {static_count}, sessions revoked {revoked}.'
        ))
