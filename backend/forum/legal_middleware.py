from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from .legal import accepted_current


class ResidentConsentMiddleware:
    """Require current documents on the first real login after rollout.

    Existing sessions reach this checkpoint when they next sign in. A pending
    checkpoint also blocks direct cabinet URLs until acceptance or withdrawal.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        was_authenticated = request.user.is_authenticated
        user = request.user
        if was_authenticated and not user.is_staff and not user.is_superuser:
            if not user.is_active:
                logout(request)
                return redirect('resident_login')
            if request.session.get('legal_pending') and request.path.startswith('/admin/cabinet/'):
                allowed = {reverse('resident_legal_accept'), reverse('resident_legal_profile'),
                           reverse('resident_logout'), reverse('account_logout')}
                if request.path not in allowed:
                    return redirect('resident_legal_accept')
        response = self.get_response(request)
        user = request.user
        if not was_authenticated and user.is_authenticated and not user.is_staff and not user.is_superuser:
            pending = not all(accepted_current(user, kind) for kind in ('consent', 'rules'))
            request.session['legal_pending'] = pending
            if pending:
                destination = response.get('Location', '')
                if destination.startswith('/admin/cabinet/') and not destination.startswith('//'):
                    request.session['legal_next'] = destination
                return redirect('resident_legal_accept')
        return response
