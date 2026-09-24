from django.contrib import admin

from .admin import RecordedAdmin
from .controller_scope import ControllerLineAccess
from .models import User


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
