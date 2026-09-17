import csv

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.admin.models import LogEntry
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect
from django.contrib import messages
from django.utils import timezone
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    Account, GroupConsumption, LandPlot, Membership, Meter, Person,
    PlotRelation, Reading, SupplyNode, User, WaterGroup,
)

admin.site.site_header = 'ТСН «ТРУД-1» · рабочая база'
admin.site.site_title = 'Труд-1'
admin.site.index_title = 'Реестр и учёт воды'

# OTP secrets and one-time recovery codes are deliberately not administered
# through the web interface. Recovery is an audited server-side operation.
for otp_model in (TOTPDevice, StaticDevice):
    try:
        admin.site.unregister(otp_model)
    except admin.sites.NotRegistered:
        pass

@admin.register(User)
class StaffAdmin(UserAdmin):
    """Only the technical superuser can grant or revoke access."""
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return ('username',) if obj else ()


@admin.register(LogEntry)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ('action_time', 'user', 'content_type', 'object_id', 'object_repr', 'action_flag', 'details')
    list_filter = ('user', 'content_type', 'action_flag')
    search_fields = ('user__username', 'object_repr', 'object_id', 'change_message')
    date_hierarchy = 'action_time'
    list_select_related = ('user', 'content_type')
    actions = None

    @admin.display(description='Действие и причина')
    def details(self, obj):
        return obj.get_change_message()

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False



class RecordedForm(forms.ModelForm):
    change_reason = forms.CharField(label='Причина исправления', required=False, widget=forms.Textarea(attrs={'rows': 2}))

    class Meta:
        fields = '__all__'
        widgets = {'version': forms.HiddenInput()}

    def clean(self):
        data = super().clean()
        if self.instance.pk:
            if not data.get('change_reason', '').strip():
                self.add_error('change_reason', 'При изменении существующей записи укажите причину.')
            current = type(self.instance).objects.get(pk=self.instance.pk)
            if current.version != data.get('version'):
                raise forms.ValidationError('Другой сеанс уже изменил запись. Обновите страницу перед сохранением.')
        return data


class RecordedAdmin(SimpleHistoryAdmin):
    form = RecordedForm
    list_per_page = 30
    actions = None

    def history_form_view(self, request, object_id, version_id, extra_context=None):
        # The library's hidden revert button is not a server-side POST guard.
        if request.method != 'GET' and request.method != 'HEAD':
            raise PermissionDenied
        return super().history_form_view(request, object_id, version_id, extra_context)

    def get_readonly_fields(self, request, obj=None):
        return tuple(super().get_readonly_fields(request, obj)) + ('created_by_info', 'changed_by_info')

    @staticmethod
    def stamp(record):
        if record is None:
            return 'Нет истории'
        actor = record.history_user
        name = (actor.get_full_name() or actor.username) if actor else 'Автор не указан (старые данные / системная операция)'
        return f'{name} · {timezone.localtime(record.history_date):%d.%m.%Y %H:%M:%S}'

    @admin.display(description='Кто и когда создал')
    def created_by_info(self, obj):
        if not obj.pk:
            return 'Будет записано при сохранении'
        return self.stamp(obj.history.order_by('history_date', 'history_id').select_related('history_user').first())

    @admin.display(description='Кто и когда изменил последним')
    def changed_by_info(self, obj):
        if not obj.pk:
            return 'Будет записано при сохранении'
        return self.stamp(obj.history.order_by('-history_date', '-history_id').select_related('history_user').first())

    def get_list_display(self, request):
        return tuple(super().get_list_display(request)) + ('changed_by_info',)

    def log_change(self, request, obj, message):
        reason = getattr(obj, '_change_reason', '')
        if isinstance(message, list):
            message = [*message, {'changed': {'fields': [f'Причина: {reason}']}}]
        else:
            message = f'{message}; Причина: {reason}'
        return super().log_change(request, obj, message)

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        try:
            return super().changeform_view(request, object_id, form_url, extra_context)
        except (ValidationError, IntegrityError):
            if request.method != 'POST':
                raise
            messages.error(request, 'Сохранение отменено: данные изменились или нарушено ограничение. Проверьте актуальную запись и повторите ввод.')
            return HttpResponseRedirect(request.path)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        if change and not self.has_change_permission(request, obj):
            raise PermissionDenied
        if not change and not self.has_add_permission(request):
            raise PermissionDenied
        obj._history_user = request.user
        obj._change_reason = form.cleaned_data.get('change_reason') or 'Создание записи'
        super().save_model(request, obj, form, change)


@admin.register(Account)
class AccountAdmin(RecordedAdmin):
    list_display = (
        'id', 'number', 'plot', 'contact_name', 'phone',
        'groups_today', 'latest_group_consumption', 'archived',
    )
    search_fields = ('=id', 'number', 'plot', 'contact_name', 'phone')
    list_filter = ('archived',)
    readonly_fields = ('id', 'groups_today', 'latest_group_consumption', 'linked_plots')
    actions = ['export_accounts']

    @admin.display(description='Привязанные участки')
    def linked_plots(self, obj):
        return ', '.join(obj.land_plots.values_list('label', flat=True)) or '—'

    @admin.display(description='В группе сейчас')
    def groups_today(self, obj):
        from django.db.models import Q
        today = timezone.localdate()
        return ', '.join(Membership.objects.filter(account=obj, starts__lte=today).filter(
            Q(ends__isnull=True) | Q(ends__gt=today)
        ).values_list('group__name', flat=True)) or '—'

    @admin.display(description='Последние кубы текущей группы')
    def latest_group_consumption(self, obj):
        from django.db.models import Q
        today = timezone.localdate()
        membership = Membership.objects.filter(account=obj, starts__lte=today).filter(
            Q(ends__isnull=True) | Q(ends__gt=today)
        ).select_related('group').first()
        if membership is None:
            return '—'
        latest = GroupConsumption.objects.filter(group=membership.group).order_by('-ends', '-starts').first()
        if latest is None:
            return f'{membership.group.name}: данных ещё нет'
        return (
            f'{membership.group.name}: {latest.volume} м³ '
            f'за {latest.starts:%d.%m.%Y}–{latest.ends:%d.%m.%Y}'
        )

    def has_export_permission(self, request):
        return request.user.has_perm('water.export_account')

    @admin.action(description='Выгрузить выбранные карточки в CSV', permissions=['export'])
    def export_accounts(self, request, queryset):
        if not self.has_export_permission(request):
            raise PermissionDenied
        from django.contrib.contenttypes.models import ContentType
        LogEntry.objects.create(user=request.user,
            content_type=ContentType.objects.get_for_model(Account),
            object_id='', object_repr='Выгрузка карточек', action_flag=2,
            change_message=f'Экспорт CSV: {queryset.count()} записей')
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="trud-accounts.csv"'
        response['Cache-Control'] = 'no-store'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow(['ID', 'Лицевой счёт', 'Участок', 'ФИО контакта', 'Телефон'])
        def safe(value):
            text = str(value or '')
            return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text
        for row in queryset.order_by('id').values_list('id', 'number', 'plot', 'contact_name', 'phone'):
            writer.writerow([safe(value) for value in row])
        return response


@admin.register(Person)
class PersonAdmin(RecordedAdmin):
    list_display = ('id', 'full_name', 'phone', 'email', 'active_relations', 'archived')
    list_filter = ('archived',)
    search_fields = ('full_name', 'phone', 'email')

    @admin.display(description='Связи сейчас')
    def active_relations(self, obj):
        today = timezone.localdate()
        return ', '.join(
            PlotRelation.objects.filter(person=obj, starts__lte=today).filter(
                Q(ends__isnull=True) | Q(ends__gt=today)
            ).select_related('plot').values_list('plot__label', flat=True)
        ) or '—'


@admin.register(LandPlot)
class LandPlotAdmin(RecordedAdmin):
    list_display = ('id', 'label', 'address', 'cadastral_number', 'account', 'current_people', 'archived')
    list_filter = ('archived',)
    search_fields = ('label', 'address', 'cadastral_number', 'account__number')
    autocomplete_fields = ('account',)

    @admin.display(description='Люди сейчас')
    def current_people(self, obj):
        today = timezone.localdate()
        return ', '.join(
            PlotRelation.objects.filter(plot=obj, starts__lte=today).filter(
                Q(ends__isnull=True) | Q(ends__gt=today)
            ).select_related('person').values_list('person__full_name', flat=True)
        ) or '—'


@admin.register(PlotRelation)
class PlotRelationAdmin(RecordedAdmin):
    list_display = ('person', 'plot', 'role', 'starts', 'ends', 'document')
    list_filter = ('role', 'plot')
    search_fields = ('person__full_name', 'plot__label', 'document')
    autocomplete_fields = ('person', 'plot')



@admin.register(SupplyNode)
class SupplyNodeAdmin(RecordedAdmin):
    search_fields = ('name',)


@admin.register(WaterGroup)
class WaterGroupAdmin(RecordedAdmin):
    list_display = ('id', 'name', 'node', 'source')
    list_filter = ('node', 'source')
    search_fields = ('name',)
    autocomplete_fields = ('node',)


@admin.register(Membership)
class MembershipAdmin(RecordedAdmin):
    list_display = ('account', 'group', 'starts', 'ends')
    list_filter = ('group',)
    autocomplete_fields = ('account', 'group')


@admin.register(Meter)
class MeterAdmin(RecordedAdmin):
    list_display = ('serial', 'kind', 'node', 'group', 'account', 'retired_on')
    list_filter = ('kind', 'node')
    search_fields = ('serial', 'account__number', 'account__plot')
    autocomplete_fields = ('node', 'group', 'account')


@admin.register(Reading)
class ReadingAdmin(RecordedAdmin):
    list_display = ('meter', 'date', 'value', 'interval_consumption')
    list_filter = ('meter__kind', 'meter__node')
    date_hierarchy = 'date'
    autocomplete_fields = ('meter',)
    search_fields = ('meter__serial', 'meter__account__number', 'meter__account__plot')
    readonly_fields = ('interval_consumption',)

    @admin.display(description='Расход с предыдущего показания, м³')
    def interval_consumption(self, obj):
        if not obj.pk:
            return 'Появится после сохранения'
        value = obj.consumption
        return 'Неизвестен — начальное показание' if value is None else value


@admin.register(GroupConsumption)
class GroupConsumptionAdmin(RecordedAdmin):
    list_display = ('group', 'starts', 'ends', 'volume', 'reported_by')
    list_filter = ('group__node', 'group')
    search_fields = ('group__name', 'reported_by')
    autocomplete_fields = ('group',)
