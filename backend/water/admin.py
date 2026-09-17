import csv
from urllib.parse import urlencode

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.admin.models import LogEntry
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseRedirect
from django.contrib import messages
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    Account, BillingAssignment, BillingPeriod, BillingPolicy, Charge,
    GroupConsumption, LandPlot, Membership, Meter, Payment, PaymentAllocation,
    Person, PlotRelation, Reading, SupplyNode, Tariff, User, WaterGroup,
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


class WaterWorkspaceFilterForm(forms.Form):
    date = forms.DateField(
        label='Дата показаний', widget=forms.DateInput(attrs={'type': 'date'}),
    )
    node = forms.ModelChoiceField(
        label='Узел', queryset=SupplyNode.objects.all().order_by('name'), required=False,
        empty_label='Все узлы',
    )
    group = forms.ModelChoiceField(
        label='Группа', queryset=WaterGroup.objects.all().order_by('name'), required=False,
        empty_label='Все группы',
    )
    kind = forms.ChoiceField(
        label='Назначение', required=False,
        choices=[('', 'Все назначения'), *Meter._meta.get_field('kind').choices],
    )
    status = forms.ChoiceField(
        label='Состояние', required=False,
        choices=[('', 'Все'), ('missing', 'Не внесено'), ('entered', 'Уже внесено')],
    )
    q = forms.CharField(label='Поиск', required=False)


def validation_text(error):
    if hasattr(error, 'message_dict'):
        return '; '.join(message for values in error.message_dict.values() for message in values)
    return '; '.join(error.messages)


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
    list_display = ('id', 'label', 'address', 'cadastral_number', 'area_m2', 'account', 'current_people', 'archived')
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
    list_display = (
        'serial', 'kind', 'node', 'group', 'account', 'commissioned_on',
        'seal_number', 'last_reading', 'retired_on',
    )
    list_filter = ('kind', 'node', 'group', 'commissioned_on', 'retired_on')
    search_fields = ('serial', 'seal_number', 'account__number', 'account__plot', 'group__name')
    autocomplete_fields = ('node', 'group', 'account')

    @admin.display(description='Последнее показание')
    def last_reading(self, obj):
        reading = obj.reading_set.order_by('-date', '-id').first()
        return f'{reading.value} м³ · {reading.date:%d.%m.%Y}' if reading else 'Нет показаний'


@admin.register(Reading)
class ReadingAdmin(RecordedAdmin):
    change_list_template = 'admin/water/reading/change_list.html'
    list_display = ('meter', 'account_info', 'date', 'value', 'interval_consumption')
    list_filter = ('meter__kind', 'meter__node')
    date_hierarchy = 'date'
    autocomplete_fields = ('meter',)
    search_fields = ('meter__serial', 'meter__account__number', 'meter__account__plot')
    readonly_fields = ('interval_consumption',)
    actions = ['export_readings']

    def get_urls(self):
        return [
            path(
                'workspace/', self.admin_site.admin_view(self.workspace_view),
                name='water_reading_workspace',
            ),
        ] + super().get_urls()

    @admin.display(description='Лицевой счёт / участок')
    def account_info(self, obj):
        if obj.meter.account is None:
            return '—'
        account = obj.meter.account
        return ' · '.join(filter(None, (account.number, account.plot))) or f'ID {account.pk}'

    @admin.display(description='Расход с предыдущего показания, м³')
    def interval_consumption(self, obj):
        if not obj.pk:
            return 'Появится после сохранения'
        value = obj.consumption
        return 'Неизвестен — начальное показание' if value is None else value

    def has_export_permission(self, request):
        return request.user.has_perm('water.export_reading')

    @admin.action(description='Выгрузить выбранные показания в CSV', permissions=['export'])
    def export_readings(self, request, queryset):
        if not self.has_export_permission(request):
            raise PermissionDenied
        return self.csv_response(request, queryset.select_related('meter', 'meter__account', 'meter__group'))

    def csv_response(self, request, queryset, label='Экспорт показаний'):
        from django.contrib.contenttypes.models import ContentType
        count = queryset.count()
        LogEntry.objects.create(
            user=request.user, content_type=ContentType.objects.get_for_model(Reading),
            object_id='', object_repr=label, action_flag=2,
            change_message=f'Экспорт CSV: {count} записей',
        )
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="trud-water-readings.csv"'
        response['Cache-Control'] = 'no-store'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')
        writer.writerow([
            'Счётчик', 'Назначение', 'Лицевой счёт', 'Участок', 'Группа',
            'Дата', 'Показание, м³', 'Расход, м³', 'Примечание',
        ])

        def safe(value):
            text = str(value or '')
            return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text

        for reading in queryset.order_by('date', 'meter__serial', 'id'):
            account = reading.meter.account
            writer.writerow(map(safe, [
                reading.meter.serial, reading.meter.get_kind_display(),
                account.number if account else '', account.plot if account else '',
                reading.meter.group.name if reading.meter.group else '',
                reading.date.isoformat(), reading.value,
                reading.consumption if reading.consumption is not None else '', reading.notes,
            ]))
        return response

    def workspace_view(self, request):
        if not self.has_view_permission(request) or not self.has_add_permission(request):
            raise PermissionDenied

        source = request.POST if request.method == 'POST' else request.GET
        initial = {'date': timezone.localdate()}
        filter_form = WaterWorkspaceFilterForm(source or None, initial=initial)
        if filter_form.is_valid():
            selected_date = filter_form.cleaned_data['date']
            filters = filter_form.cleaned_data
        else:
            selected_date = timezone.localdate()
            filters = {'node': None, 'group': None, 'kind': '', 'status': '', 'q': ''}

        meters = Meter.objects.select_related('node', 'group', 'account').filter(
            Q(commissioned_on__isnull=True) | Q(commissioned_on__lte=selected_date),
        ).filter(Q(retired_on__isnull=True) | Q(retired_on__gte=selected_date))
        if filters.get('node'):
            meters = meters.filter(node=filters['node'])
        if filters.get('group'):
            meters = meters.filter(group=filters['group'])
        if filters.get('kind'):
            meters = meters.filter(kind=filters['kind'])
        if filters.get('q'):
            query = filters['q'].strip()
            meters = meters.filter(
                Q(serial__icontains=query) | Q(seal_number__icontains=query)
                | Q(account__number__icontains=query) | Q(account__plot__icontains=query)
                | Q(group__name__icontains=query)
            )
        meters = list(meters.order_by('node__name', 'group__name', 'account__plot', 'serial', 'id'))

        readings = Reading.objects.filter(meter_id__in=[meter.pk for meter in meters]).order_by('meter_id', 'date', 'id')
        previous, existing, following = {}, {}, {}
        for reading in readings:
            if reading.date < selected_date:
                previous[reading.meter_id] = reading
            elif reading.date == selected_date:
                existing[reading.meter_id] = reading
            elif reading.meter_id not in following:
                following[reading.meter_id] = reading

        errors = {}
        posted_values = {}
        posted_notes = {}
        if request.method == 'POST' and filter_form.is_valid():
            value_field = forms.DecimalField(max_digits=14, decimal_places=3, min_value=0, localize=True)
            candidates = []
            for meter in meters:
                raw_value = request.POST.get(f'value_{meter.pk}', '').strip()
                posted_values[meter.pk] = raw_value
                posted_notes[meter.pk] = request.POST.get(f'notes_{meter.pk}', '').strip()
                if not raw_value:
                    continue
                if meter.pk in existing:
                    errors[meter.pk] = 'За эту дату показание уже существует.'
                    continue
                try:
                    value = value_field.clean(raw_value)
                    reading = Reading(
                        meter=meter, date=selected_date, value=value,
                        notes=posted_notes[meter.pk],
                    )
                    reading.full_clean()
                    candidates.append(reading)
                except ValidationError as error:
                    errors[meter.pk] = validation_text(error)

            if not errors and candidates:
                try:
                    with transaction.atomic():
                        for reading in candidates:
                            reading._history_user = request.user
                            reading._change_reason = 'Пакетный ввод показаний'
                            reading.save()
                            self.log_addition(request, reading, 'Пакетный ввод показаний')
                except (ValidationError, IntegrityError):
                    messages.error(request, 'Сохранение отменено: данные успели измениться. Обновите страницу и повторите ввод.')
                else:
                    messages.success(request, f'Сохранено показаний: {len(candidates)}.')
                    query = urlencode({
                        key: value.pk if hasattr(value, 'pk') else value
                        for key, value in filters.items() if value not in (None, '')
                    } | {'date': selected_date.isoformat()})
                    return HttpResponseRedirect(f'{reverse("admin:water_reading_workspace")}?{query}')
            elif not errors:
                messages.warning(request, 'Введите хотя бы одно новое показание.')

        rows = []
        for meter in meters:
            current = existing.get(meter.pk)
            if filters.get('status') == 'missing' and current:
                continue
            if filters.get('status') == 'entered' and not current:
                continue
            account = meter.account
            rows.append({
                'meter': meter, 'account': account, 'previous': previous.get(meter.pk),
                'current': current, 'following': following.get(meter.pk),
                'error': errors.get(meter.pk), 'posted_value': posted_values.get(meter.pk, ''),
                'posted_notes': posted_notes.get(meter.pk, ''),
            })

        if request.method == 'GET' and request.GET.get('export') == 'csv':
            if not self.has_export_permission(request):
                raise PermissionDenied
            queryset = Reading.objects.filter(pk__in=[row['current'].pk for row in rows if row['current']])
            return self.csv_response(request, queryset.select_related('meter', 'meter__account', 'meter__group'), 'Отчёт рабочего места')

        context = {
            **self.admin_site.each_context(request), 'title': 'Рабочее место оператора воды',
            'opts': self.model._meta, 'filter_form': filter_form, 'selected_date': selected_date,
            'rows': rows, 'total_count': len(meters), 'entered_count': len(existing),
            'missing_count': len(meters) - len(existing), 'can_export': self.has_export_permission(request),
        }
        return TemplateResponse(request, 'admin/water/reading/workspace.html', context)


@admin.register(GroupConsumption)
class GroupConsumptionAdmin(RecordedAdmin):
    list_display = ('group', 'starts', 'ends', 'volume', 'reported_by')
    list_filter = ('group__node', 'group')
    search_fields = ('group__name', 'reported_by')
    autocomplete_fields = ('group',)


@admin.register(BillingPolicy)
class BillingPolicyAdmin(RecordedAdmin):
    list_display = ('name', 'is_default', 'missing_reading', 'loss_distribution', 'rounding', 'payment_allocation')
    list_filter = ('missing_reading', 'loss_distribution', 'rounding', 'payment_allocation')
    search_fields = ('name', 'notes')


@admin.register(BillingAssignment)
class BillingAssignmentAdmin(RecordedAdmin):
    list_display = ('policy', 'account', 'group', 'starts', 'ends', 'priority')
    list_filter = ('policy', 'group')
    search_fields = ('account__number', 'account__plot', 'group__name', 'policy__name')
    autocomplete_fields = ('policy', 'account', 'group')


@admin.register(Tariff)
class TariffAdmin(RecordedAdmin):
    list_display = ('name', 'rate', 'starts', 'ends', 'account', 'group')
    list_filter = ('starts', 'ends', 'group')
    search_fields = ('name', 'account__number', 'account__plot', 'group__name', 'notes')
    autocomplete_fields = ('account', 'group')


@admin.register(BillingPeriod)
class BillingPeriodAdmin(RecordedAdmin):
    list_display = ('starts', 'ends', 'status')
    list_filter = ('status',)
    search_fields = ('starts', 'ends')
    actions = ('calculate_drafts',)

    @admin.action(description='Рассчитать или обновить безопасные черновики')
    def calculate_drafts(self, request, queryset):
        from .billing import calculate_period
        created = updated = review = skipped = 0
        for period in queryset.order_by('starts'):
            try:
                results = calculate_period(period, actor=request.user)
            except ValidationError as error:
                messages.error(request, f'{period}: {validation_text(error)}')
                continue
            for result in results:
                if result.outcome == 'created':
                    created += 1
                elif result.outcome == 'updated':
                    updated += 1
                elif result.outcome == 'review':
                    review += 1
                else:
                    skipped += 1
        messages.success(
            request,
            f'Черновики: создано {created}, обновлено {updated}; требуют проверки {review}, пропущено без правил/тарифа {skipped}.',
        )


@admin.register(Charge)
class ChargeAdmin(RecordedAdmin):
    list_display = ('account', 'period', 'kind', 'volume', 'rate', 'amount', 'status', 'origin')
    list_filter = ('status', 'origin', 'kind', 'period')
    search_fields = ('account__number', 'account__plot', 'notes', 'calculation')
    autocomplete_fields = ('account', 'period')
    readonly_fields = ('source_key',)


@admin.register(Payment)
class PaymentAdmin(RecordedAdmin):
    list_display = ('account', 'paid_on', 'amount', 'method', 'status', 'reference')
    list_filter = ('status', 'method', 'paid_on')
    search_fields = ('account__number', 'account__plot', 'reference', 'notes')
    autocomplete_fields = ('account',)


@admin.register(PaymentAllocation)
class PaymentAllocationAdmin(RecordedAdmin):
    list_display = ('payment', 'charge', 'amount')
    search_fields = ('payment__account__number', 'charge__account__number')
    autocomplete_fields = ('payment', 'charge')
