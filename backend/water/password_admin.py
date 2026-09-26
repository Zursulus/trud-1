from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse

from .models import User
from .portal import issue_password_reset, password_reset_allowed


def _can_manage_passwords(user):
    return bool(user.is_superuser or user.has_perm('water.add_residentpasswordreset'))


def password_access_admin(request):
    if not _can_manage_passwords(request.user):
        raise PermissionDenied

    candidates = [
        user for user in User.objects.filter(is_active=True, is_superuser=False).order_by('username')
        if password_reset_allowed(user)
    ]
    reset_url = None
    target = None
    error = None

    if request.method == 'POST':
        target_id = request.POST.get('user_id')
        if not target_id:
            raise PermissionDenied
        target = get_object_or_404(User, pk=target_id)
        if not password_reset_allowed(target):
            raise PermissionDenied
        try:
            reset, raw = issue_password_reset(target, actor=request.user)
        except ValidationError as validation_error:
            error = '; '.join(validation_error.messages)
        else:
            reset_url = request.build_absolute_uri(reverse('resident_password_reset', args=[raw]))

    context = {
        **admin.site.each_context(request),
        'title': 'Доступ и пароли',
        'candidates': candidates,
        'target': target,
        'reset_url': reset_url,
        'error': error,
    }
    return TemplateResponse(request, 'admin/water/password_access.html', context)
