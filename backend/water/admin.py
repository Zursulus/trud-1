import csv

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpResponse, HttpResponseRedirect
from django.contrib import messages
from django.utils import timezone
from simple_history.admin import SimpleHistoryAdmin

from .models import Account, GroupConsumption, Membership, Meter, Reading, SupplyNode, User, WaterGroup

admin.site.site_header = 'СНТ «Труд-1» · рабочая база'
admin.site.site_title = 'Труд-1'
admin.site.index_title = 'Реестр и учёт воды'
admin.site.register(User, UserAdmin)


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
        obj._history_user = request.user
        obj._change_reason = form.cleaned_data.get('change_reason') or 'Создание записи'
        super().save_model(request, obj, form, change)


@admin.register(Account)
class AccountAdmin(RecordedAdmin):
    list_display = ('id', 'number', 'plot', 'contact_name', 'phone', 'groups_today', 'archived')
    search_fields = ('=id', 'number', 'plot', 'contact_name', 'phone')
    list_filter = ('archived',)
    readonly_fields = ('id', 'groups_today')
    actions = ['export_accounts']

    @admin.display(description='В группе сейчас')
    def groups_today(self, obj):
        from django.db.models import Q
        today = timezone.localdate()
        return ', '.join(Membership.objects.filter(account=obj, starts__lte=today).filter(
            Q(ends__isnull=True) | Q(ends__gt=today)
        ).values_list('group__name', flat=True)) or '—'

    @admin.action(description='Выгрузить выбранные карточки в CSV', permissions=['view'])
    def export_accounts(self, request, queryset):
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
