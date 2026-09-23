import csv

from django.contrib import admin
from django.contrib.admin.models import LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse

from .admin import spreadsheet_safe
from .models import Account, ImportBatch, ImportRow, LandPlot, Person, PlotRelation
from .private_registry import MemberRegistryEntry


PRIVATE_REGISTRY_PERMISSION = 'water.access_private_registry'
LEGACY_ACCOUNT_PII_FIELDS = ('contact_name', 'phone')


def has_private_registry_access(user):
    return bool(user.is_superuser or user.has_perm(PRIVATE_REGISTRY_PERMISSION))


def take_registered_admin(model):
    instance = admin.site._registry.get(model)
    base = type(instance) if instance is not None else admin.ModelAdmin
    if instance is not None:
        admin.site.unregister(model)
    return base


class PrivateRegistryPermissionMixin:
    """Require the explicit sensitive-data permission in addition to model perms."""

    def has_module_permission(self, request):
        return has_private_registry_access(request.user) and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return has_private_registry_access(request.user) and super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return has_private_registry_access(request.user) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return has_private_registry_access(request.user) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


AccountAdminBase = take_registered_admin(Account)


@admin.register(Account)
class PrivacyAccountAdmin(AccountAdminBase):
    """Keep legacy contact PII out of the ordinary operational workspace."""

    def get_list_display(self, request):
        fields = tuple(super().get_list_display(request))
        if has_private_registry_access(request.user):
            return fields
        return tuple(field for field in fields if field not in LEGACY_ACCOUNT_PII_FIELDS)

    def get_search_fields(self, request):
        fields = tuple(super().get_search_fields(request))
        if has_private_registry_access(request.user):
            return fields
        return tuple(field for field in fields if field not in LEGACY_ACCOUNT_PII_FIELDS)

    def get_exclude(self, request, obj=None):
        inherited = tuple(super().get_exclude(request, obj) or ())
        if has_private_registry_access(request.user):
            return inherited or None
        return tuple(dict.fromkeys((*inherited, *LEGACY_ACCOUNT_PII_FIELDS)))

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        if has_private_registry_access(request.user):
            fields = tuple(dict.fromkeys((*fields, *LEGACY_ACCOUNT_PII_FIELDS)))
        return fields

    @admin.action(
        description='Выгрузить выбранные карточки в CSV без персональных данных',
        permissions=['export'],
    )
    def export_accounts(self, request, queryset):
        if not self.has_export_permission(request):
            raise PermissionDenied
        LogEntry.objects.create(
            user=request.user,
            content_type=ContentType.objects.get_for_model(Account),
            object_id='',
            object_repr='Выгрузка карточек без ПД',
            action_flag=2,
            change_message=f'Экспорт CSV: {queryset.count()} записей',
        )
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="trud-accounts.csv"'
        response['Cache-Control'] = 'no-store'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow(['ID', 'Лицевой счёт', 'Участок'])
        for account_id, number, plot in queryset.order_by('id').values_list('id', 'number', 'plot'):
            writer.writerow([
                spreadsheet_safe(account_id),
                spreadsheet_safe(number),
                spreadsheet_safe(plot),
            ])
        return response


LandPlotAdminBase = take_registered_admin(LandPlot)


@admin.register(LandPlot)
class PrivacyLandPlotAdmin(LandPlotAdminBase):
    def get_list_display(self, request):
        fields = tuple(super().get_list_display(request))
        if has_private_registry_access(request.user):
            return fields
        return tuple(field for field in fields if field != 'current_people')


PersonAdminBase = take_registered_admin(Person)


@admin.register(Person)
class PrivatePersonAdmin(PrivateRegistryPermissionMixin, PersonAdminBase):
    pass


PlotRelationAdminBase = take_registered_admin(PlotRelation)


@admin.register(PlotRelation)
class PrivatePlotRelationAdmin(PrivateRegistryPermissionMixin, PlotRelationAdminBase):
    pass


ImportBatchAdminBase = take_registered_admin(ImportBatch)


@admin.register(ImportBatch)
class PrivateImportBatchAdmin(PrivateRegistryPermissionMixin, ImportBatchAdminBase):
    pass


ImportRowAdminBase = take_registered_admin(ImportRow)


@admin.register(ImportRow)
class PrivateImportRowAdmin(PrivateRegistryPermissionMixin, ImportRowAdminBase):
    pass


@admin.register(MemberRegistryEntry)
class MemberRegistryEntryAdmin(PrivateRegistryPermissionMixin, admin.ModelAdmin):
    list_display = (
        'resident_number', 'account', 'phone', 'email', 'joined_year', 'person', 'created_at',
    )
    search_fields = (
        '=resident_number__number', 'account__number', 'account__plot', 'phone', 'email',
        'person__full_name',
    )
    autocomplete_fields = ('account', 'person')
    readonly_fields = ('created_at',)
    list_select_related = ('resident_number', 'account', 'person')

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        if obj:
            return tuple(dict.fromkeys((*fields, 'resident_number')))
        return fields
