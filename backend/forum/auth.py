"""Small safety boundary around the stock allauth code flows."""
from functools import wraps

from allauth.account.auth_backends import AuthenticationBackend
from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.forms import RequestLoginCodeForm
from allauth.account.models import EmailAddress
from allauth.utils import build_absolute_uri
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.urls import reverse


UNAVAILABLE = 'Вход по коду для этого адреса недоступен. Обратитесь в правление; сотрудники используют служебный вход.'


def resident_for_email(email):
    users = list(get_user_model().objects.filter(
        Q(email__iexact=email) | Q(emailaddress__email__iexact=email),
    ).distinct())
    if len(users) > 1 or any(
        u.is_staff or u.is_superuser or u.groups.filter(name='Администратор ТСН').exists()
        for u in users
    ):
        raise ValidationError(UNAVAILABLE)
    return users[0] if users else None


class EmailCodeBackend(AuthenticationBackend):
    # Code flows call adapter.login directly. Never add email/password fallback
    # to the existing admin, workspace or resident password authentication.
    def authenticate(self, request, **credentials):
        return None

    def get_user(self, user_id):
        user = super().get_user(user_id)
        # A resident session must not become a staff session after promotion.
        if user and (user.is_staff or user.is_superuser or user.groups.filter(name='Администратор ТСН').exists()):
            return None
        return user


class ResidentAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request):
        return settings.RESIDENT_EMAIL_LOGIN_ENABLED

    def clean_email(self, email):
        email = super().clean_email(email)
        resident_for_email(email)
        return email

    def pre_login(self, request, user, **kwargs):
        self._check_user(user, email=kwargs.get('email'))
        return super().pre_login(request, user, **kwargs)

    def login(self, request, user):
        # Recheck at completion: permissions/email may have changed while a code was pending.
        self._check_user(user)
        return super().login(request, user)

    def _check_user(self, user, email=None):
        if not settings.RESIDENT_EMAIL_LOGIN_ENABLED or not user.is_active or user.is_staff or user.is_superuser:
            raise PermissionDenied(UNAVAILABLE)
        if email and email.casefold() != user.email.casefold():
            raise PermissionDenied(UNAVAILABLE)
        try:
            if resident_for_email(user.email) != user:
                raise PermissionDenied(UNAVAILABLE)
        except ValidationError as exc:
            raise PermissionDenied(UNAVAILABLE) from exc

    def send_account_already_exists_mail(self, email):
        # Passwordless residents have no password-reset route.
        self.send_mail('account/email/account_already_exists', email, {
            'login_url': build_absolute_uri(self.request, reverse('account_login')),
        })

    def get_login_redirect_url(self, request):
        return reverse('forum:index' if settings.FORUM_ENABLED else 'resident_dashboard')

    def get_signup_redirect_url(self, request):
        return self.get_login_redirect_url(request)

    def get_logout_redirect_url(self, request):
        return reverse('account_login')


class ResidentRequestLoginCodeForm(RequestLoginCodeForm):
    def clean_email(self):
        email = super().clean_email()
        user = resident_for_email(email)
        # Do not let allauth prefer a verified record when legacy users share an email.
        if user is not None and email.casefold() != user.email.casefold():
            raise ValidationError(UNAVAILABLE)
        if user is not None and user.is_active:
            self._user = user
            EmailAddress.objects.get_or_create(user=user, email=email.lower(), defaults={'primary': True})
        return email


def email_login_enabled(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not settings.RESIDENT_EMAIL_LOGIN_ENABLED:
            raise Http404
        if request.user.is_authenticated and (request.user.is_staff or request.user.is_superuser):
            raise PermissionDenied(UNAVAILABLE)
        return view(request, *args, **kwargs)
    return wrapped


from allauth.account.forms import SignupForm
from django.db import transaction
from .legal import ConsentForm


class ResidentSignupForm(SignupForm, ConsentForm):
    @transaction.atomic
    def save(self, request):
        user = super().save(request)
        self.record(request, user)
        return user
