from types import MethodType

from django.contrib import admin
from django.http import HttpResponseRedirect
from django.urls import reverse

from .admin import RecordedAdmin
from .controller_scope import ControllerLineAccess
from .models import ControllerReadingSubmission, User


@admin.register(ControllerLineAccess)
class ControllerLineAccessAdmin(RecordedAdmin):
    list_display = ('group', 'user', 'starts', 'ends')
    list_filter = ('group', 'starts', 'ends')
    search_fields = ('group__name', 'user__username', 'user__email', 'user__first_name', 'user__last_name')
    autocomplete_fields = ('group', 'user')

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'user':
            kwargs['queryset'] = User.objects.filter(is_staff=True, is_active=True).order_by(
                'last_name', 'first_name', 'username',
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


# Keep the legacy single-reading capture available for existing generic water
# controllers, but never let an explicitly line-scoped senior escape into its
# global meter list. Assigned seniors are redirected to the scoped workspace.
submission_admin = admin.site._registry.get(ControllerReadingSubmission)
if submission_admin is not None and not getattr(submission_admin, '_line_scope_guard_installed', False):
    original_capture_view = submission_admin.capture_view

    def scoped_capture_view(self, request):
        if (
            request.user.is_authenticated
            and not request.user.is_superuser
            and ControllerLineAccess.objects.filter(user=request.user).exists()
        ):
            return HttpResponseRedirect(reverse('water_controller_workspace'))
        return original_capture_view(request)

    submission_admin.capture_view = MethodType(scoped_capture_view, submission_admin)
    submission_admin._line_scope_guard_installed = True
