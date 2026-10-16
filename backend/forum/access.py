from functools import wraps

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.urls import reverse
from django.views.decorators.cache import never_cache


def can_moderate(user):
    return user.is_authenticated and user.is_active and user.has_perm('forum.moderate_forum')


def can_write(user):
    return user.is_authenticated and user.is_active and bool(user.email) and EmailAddress.objects.filter(
        user=user, email__iexact=user.email, verified=True,
    ).exists()


def require_writer(user):
    if not can_write(user):
        raise PermissionDenied('Для записи подтвердите почту через вход по коду.')


def forum_view(view):
    @wraps(view)
    @never_cache
    def wrapped(request, *args, **kwargs):
        if not settings.FORUM_ENABLED:
            raise Http404
        if not settings.FORUM_PUBLIC_READ and not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), reverse(
                'account_login' if settings.RESIDENT_EMAIL_LOGIN_ENABLED else 'resident_login',
            ))
        return view(request, *args, **kwargs)
    return wrapped


def navigation(request):
    context = {
        'forum_enabled': settings.FORUM_ENABLED,
        'resident_email_login_enabled': settings.RESIDENT_EMAIL_LOGIN_ENABLED,
        'forum_moderator': settings.FORUM_ENABLED and can_moderate(request.user),
    }

    match = request.resolver_match
    if settings.FORUM_ENABLED and match and match.app_name == 'forum':
        # Resolve cabinet links with the same grant rules as the resident views.
        # A public reader or a user without account access still gets the shell.
        context.update(forum_shell=True, active_section='forum')
        if request.user.is_authenticated:
            from water.portal_permissions import CAP_VIEW_ACCOUNT, resolved_accesses
            accesses = resolved_accesses(request.user, CAP_VIEW_ACCOUNT)
            if accesses:
                context.update(access=accesses[0], account=accesses[0].account)
    return context
