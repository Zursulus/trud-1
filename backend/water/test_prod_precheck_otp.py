"""Exercise the production aggregate against synthetic users and real OTP models."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from .models import User


@override_settings(TRUD_MFA_REQUIRED_USERNAMES=['admin'])
class ProductionOTPPrecheckTests(TestCase):
    def test_counts_active_users_once_and_only_confirmed_devices(self):
        tsn = Group.objects.create(name='Администратор ТСН')
        snt = Group.objects.create(name='Администратор СНТ')
        root = User.objects.create_user('synthetic-root', is_superuser=True)
        root.groups.add(tsn, snt)
        no_otp = User.objects.create_user('synthetic-no-otp')
        no_otp.groups.add(tsn)
        TOTPDevice.objects.create(user=no_otp, confirmed=False)
        recovery = User.objects.create_user('synthetic-recovery')
        recovery.groups.add(snt)
        StaticDevice.objects.create(user=recovery, confirmed=True)
        TOTPDevice.objects.create(user=root, confirmed=True)
        inactive = User.objects.create_user('synthetic-inactive', is_superuser=True, is_active=False)
        inactive.groups.add(tsn, snt)
        User.objects.create_user('synthetic-ordinary')
        User.objects.create_user('AdMiN')
        script = (Path(__file__).resolve().parents[2] / 'ops/prod-precheck-admin-otp.sh').read_text()
        code = script.split("shell --no-imports -c '\n", 1)[1].rsplit("\n'", 1)[0]
        output = StringIO()
        with CaptureQueriesContext(connection) as queries, redirect_stdout(output):
            exec(compile(code, 'prod-precheck-admin-otp', 'exec'), {})
        self.assertEqual(output.getvalue(), (
            'superusers=1\nsuperusers_without_confirmed_otp=0\n'
            'tsn_admins=2\ntsn_admins_without_confirmed_otp=1\n'
            'snt_admins=2\nsnt_admins_without_confirmed_otp=0\n'
            'all_admins=3\nall_admins_without_confirmed_otp=1\n'
            'required_usernames_without_confirmed_otp=1\n'
        ))
        self.assertTrue(queries.captured_queries)
        self.assertTrue(all(q['sql'].lstrip().upper().startswith('SELECT') for q in queries))

    @override_settings(TRUD_MFA_REQUIRED_USERNAMES=['ADMIN', 'required', 'inactive'])
    def test_required_usernames_count_ignores_roles_and_inactive_users(self):
        admin = User.objects.create_superuser('admin')
        TOTPDevice.objects.create(user=admin, confirmed=False)
        other = User.objects.create_user('ReQuIrEd')
        StaticDevice.objects.create(user=other, confirmed=True)
        User.objects.create_user('inactive', is_active=False)
        User.objects.create_superuser('optional')
        script = (Path(__file__).resolve().parents[2] / 'ops/prod-precheck-admin-otp.sh').read_text()
        code = script.split("shell --no-imports -c '\n", 1)[1].rsplit("\n'", 1)[0]
        output = StringIO()
        with redirect_stdout(output):
            exec(compile(code, 'prod-precheck-admin-otp', 'exec'), {})
        self.assertIn('required_usernames_without_confirmed_otp=1\n', output.getvalue())
        TOTPDevice.objects.create(user=admin, confirmed=True)
        output = StringIO()
        with redirect_stdout(output):
            exec(compile(code, 'prod-precheck-admin-otp', 'exec'), {})
        self.assertIn('required_usernames_without_confirmed_otp=0\n', output.getvalue())
