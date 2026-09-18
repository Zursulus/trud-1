import csv
from pathlib import Path
from urllib.parse import urlencode

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.admin.models import LogEntry
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.contrib import messages
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils import timezone
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice
from simple_history.admin import SimpleHistoryAdmin

from .models import (
    Account, AccountDocument, AppealCategory, BillingAssignment, BillingPeriod, BillingPolicy, Charge,
    DocumentCategory,
    ControllerReadingSubmission, GroupConsumption, ImportBatch, ImportRow, LandPlot, Membership, Meter,
    Payment, PaymentAllocation, Person, PlotRelation, Reading, ResidentAccess,
    ResidentAppeal, ResidentAppealMessage, ResidentInvite, ResidentPasswordReset,
    SupplyNode, Tariff, User, WaterGroup,
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


class ImportUploadForm(forms.Form):
    file = forms.FileField(label='CSV или XLSX', help_text='До 5 МБ и 5000 строк. Исходный файл не сохраняется на сервере.')
    effective_date = forms.DateField(
        label='Дата начала действия данных', widget=forms.DateInput(attrs={'type': 'date'}),
    )
    notes = forms.CharField(label='Примечание', required=False, widget=forms.Textarea(attrs={'rows': 2}))


class ControllerMeterChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, meter):
        account = meter.account
        identifier = account.number or f'ID {account.pk}' if account else f'счётчик {meter.pk}'
        address = account.plot if account else meter.group or meter.node
        return f'{identifier} — {address} — {meter.serial}'


class ControllerReadingCaptureForm(forms.ModelForm):
    meter = ControllerMeterChoiceField(label='Выберите участок', queryset=Meter.objects.none())

    class Meta:
        model = ControllerReadingSubmission
        fields = ('meter', 'date', 'value', 'photo', 'notes')
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'value': forms.NumberInput(attrs={'inputmode': 'decimal', 'step': '0.001', 'min': '0'}),
            'photo': forms.FileInput(attrs={'accept': 'image/*', 'capture': 'environment'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['meter'].queryset = Meter.objects.select_related('account', 'group', 'node').filter(
            kind='individual', account__archived=False,
        ).order_by('account__plot', 'account__number', 'serial')

    def clean_photo(self):
        photo = self.cleaned_data['photo']
        content_type = getattr(photo, 'content_type', '')
        if content_type and not content_type.startswith('image/'):
            raise forms.ValidationError('Приложите фотографию, а не другой файл.')
        return photo


class ResidentInviteForm(forms.Form):
    email = forms.EmailField(label='Электронная почта жителя')
    role = forms.ChoiceField(label='Основание доступа', choices=ResidentAccess._meta.get_field('role').choices)


def validation_text(error):
    if hasattr(error, 'message_dict'):
        return '; '.join(message for values in error.message_dict.values() for message in values)
    return '; '.join(error.messages)


def spreadsheet_safe(value):
    text = str(value or '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else text


@admin.register(Account)
class AccountAdmin(RecordedAdmin):
    list_display = (
        'id', 'number', 'plot', 'contact_name', 'phone',
        'groups_today', 'latest_group_consumption', 'balance_display', 'archived',
    )
    search_fields = ('=id', 'number', 'plot', 'contact_name', 'phone')
    list_filter = ('archived',)
    readonly_fields = (
        'id', 'groups_today', 'latest_group_consumption', 'linked_plots',
        'statement_link', 'resident_invite_link',
    )
    actions = ['export_accounts']

    @staticmethod
    def can_view_finance(request):
        return request.user.has_perm('water.view_charge') and request.user.has_perm('water.view_payment')

    def get_list_display(self, request):
        fields = tuple(super().get_list_display(request))
        return fields if self.can_view_finance(request) else tuple(field for field in fields if field != 'balance_display')

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        if not self.can_view_finance(request):
            fields = tuple(field for field in fields if field != 'statement_link')
        if not request.user.has_perm('water.add_residentinvite'):
            fields = tuple(field for field in fields if field != 'resident_invite_link')
        return fields

    def get_urls(self):
        return [
            path(
                '<path:object_id>/statement/', self.admin_site.admin_view(self.statement_view),
                name='water_account_statement',
            ),
            path(
                '<path:object_id>/invite/', self.admin_site.admin_view(self.invite_view),
                name='water_account_invite',
            ),
        ] + super().get_urls()

    @admin.display(description='Баланс')
    def balance_display(self, obj):
        if not obj.pk:
            return '—'
        from .billing import account_totals
        balance = account_totals(obj)['balance']
        label = 'долг' if balance > 0 else 'переплата' if balance < 0 else 'расчёт закрыт'
        return f'{balance} ₽ · {label}'

    @admin.display(description='Финансовая карточка')
    def statement_link(self, obj):
        if not obj.pk:
            return 'Появится после сохранения'
        return format_html(
            '<a class="button" href="{}">Открыть сверку и печатную квитанцию</a>',
            reverse('admin:water_account_statement', args=[obj.pk]),
        )

    @admin.display(description='Личный кабинет жителя')
    def resident_invite_link(self, obj):
        if not obj.pk:
            return 'Появится после сохранения'
        return format_html(
            '<a class="button" href="{}">Создать одноразовое приглашение</a>',
            reverse('admin:water_account_invite', args=[obj.pk]),
        )

    def invite_view(self, request, object_id):
        if not request.user.has_perm('water.add_residentinvite'):
            raise PermissionDenied
        account = self.get_object(request, object_id)
        if account is None:
            raise PermissionDenied
        form = ResidentInviteForm(request.POST or None)
        invite_url = None
        if request.method == 'POST' and form.is_valid():
            from .portal import issue_invite
            try:
                invite, raw = issue_invite(
                    account, form.cleaned_data['email'], form.cleaned_data['role'], actor=request.user,
                )
            except ValidationError as error:
                form.add_error(None, validation_text(error))
            else:
                invite_url = request.build_absolute_uri(reverse('resident_invite', args=[raw]))
        context = {
            **self.admin_site.each_context(request), 'title': f'Приглашение: {account}',
            'opts': self.model._meta, 'account': account, 'form': form, 'invite_url': invite_url,
        }
        return TemplateResponse(request, 'admin/water/account/invite.html', context)

    def statement_view(self, request, object_id):
        if not self.has_view_permission(request) or not self.can_view_finance(request):
            raise PermissionDenied
        account = self.get_object(request, object_id)
        if account is None:
            raise PermissionDenied
        from .billing import account_totals
        charges = Charge.objects.filter(account=account).select_related('period').order_by('-period__starts', '-id')
        payments = Payment.objects.filter(account=account).order_by('-paid_on', '-id')
        totals = account_totals(account)
        if request.GET.get('export') == 'csv':
            from django.contrib.contenttypes.models import ContentType
            LogEntry.objects.create(
                user=request.user, content_type=ContentType.objects.get_for_model(Account),
                object_id=str(account.pk), object_repr=str(account), action_flag=2,
                change_message='Экспорт финансовой сверки CSV',
            )
            response = HttpResponse(content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = f'attachment; filename="statement-{account.pk}.csv"'
            response['Cache-Control'] = 'no-store'
            response.write('\ufeff')
            writer = csv.writer(response, delimiter=';')
            writer.writerow(['Тип', 'Дата / период', 'Описание', 'Начислено', 'Оплачено', 'Статус'])
            for charge in charges:
                writer.writerow(map(spreadsheet_safe, [
                    'Начисление', f'{charge.period.starts:%d.%m.%Y}–{charge.period.ends:%d.%m.%Y}',
                    charge.get_kind_display(), charge.amount, '', charge.get_status_display(),
                ]))
            for payment in payments:
                writer.writerow(map(spreadsheet_safe, [
                    'Оплата', f'{payment.paid_on:%d.%m.%Y}', payment.reference,
                    '', payment.amount, payment.get_status_display(),
                ]))
            writer.writerow(['ИТОГО', '', '', totals['charges'], totals['payments'], totals['balance']])
            return response
        context = {
            **self.admin_site.each_context(request), 'title': f'Финансовая карточка: {account}',
            'opts': self.model._meta, 'account': account, 'charges': charges,
            'payments': payments, 'totals': totals,
        }
        return TemplateResponse(request, 'admin/water/account/statement.html', context)

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
        for row in queryset.order_by('id').values_list('id', 'number', 'plot', 'contact_name', 'phone'):
            writer.writerow([spreadsheet_safe(value) for value in row])
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

        for reading in queryset.order_by('date', 'meter__serial', 'id'):
            account = reading.meter.account
            writer.writerow(map(spreadsheet_safe, [
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


@admin.register(ControllerReadingSubmission)
class ControllerReadingSubmissionAdmin(RecordedAdmin):
    change_list_template = 'admin/water/controllerreadingsubmission/change_list.html'
    change_form_template = 'admin/water/controllerreadingsubmission/change_form.html'
    list_display = ('account_id_display', 'address_display', 'value', 'date', 'status', 'submitted_by', 'submitted_at')
    list_filter = ('status', 'date')
    search_fields = ('meter__account__number', 'meter__account__plot', 'meter__serial')
    readonly_fields = (
        'meter', 'date', 'value', 'photo_link', 'notes', 'status', 'submitted_by',
        'submitted_at', 'reviewed_by', 'reviewed_at', 'review_comment', 'reading',
    )
    fields = readonly_fields

    def get_urls(self):
        return [
            path('capture/', self.admin_site.admin_view(self.capture_view), name='water_controllerreading_capture'),
            path('<int:object_id>/photo/', self.admin_site.admin_view(self.photo_view), name='water_controllerreading_photo'),
            path('<int:object_id>/approve/', self.admin_site.admin_view(self.approve_view), name='water_controllerreading_approve'),
            path('<int:object_id>/reject/', self.admin_site.admin_view(self.reject_view), name='water_controllerreading_reject'),
        ] + super().get_urls()

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related('meter__account', 'submitted_by', 'reviewed_by', 'reading')
        if request.user.has_perm('water.change_controllerreadingsubmission'):
            return queryset
        return queryset.filter(submitted_by=request.user)

    def has_add_permission(self, request):
        return request.user.has_perm('water.add_controllerreadingsubmission')

    @admin.display(description='ID / лицевой счёт')
    def account_id_display(self, obj):
        account = obj.meter.account
        return account.number or f'ID {account.pk}' if account else '—'

    @admin.display(description='Адрес')
    def address_display(self, obj):
        return obj.meter.account.plot if obj.meter.account else '—'

    @admin.display(description='Фото')
    def photo_link(self, obj):
        if not obj.pk:
            return '—'
        return format_html('<a href="{}" target="_blank">Открыть фото</a>', reverse('admin:water_controllerreading_photo', args=[obj.pk]))

    def capture_view(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        form = ControllerReadingCaptureForm(request.POST or None, request.FILES or None)
        if request.method == 'POST' and form.is_valid():
            submission = form.save(commit=False)
            submission.submitted_by = request.user
            submission._history_user = request.user
            submission._change_reason = 'Подача контролёром на премодерацию'
            submission.save()
            self.log_addition(request, submission, 'Показание отправлено на проверку')
            messages.success(request, 'Готово. Показание отправлено на проверку.')
            return HttpResponseRedirect(reverse('admin:water_controllerreading_capture'))
        meter_data = {
            str(meter.pk): {
                'id': meter.account.number or f'ID {meter.account.pk}',
                'address': meter.account.plot or 'Адрес не заполнен',
                'serial': meter.serial,
            }
            for meter in self._capture_meters(form)
        }
        context = {
            **self.admin_site.each_context(request), 'title': 'Внести показание',
            'opts': self.model._meta, 'form': form, 'meter_data': meter_data,
        }
        return TemplateResponse(request, 'admin/water/controllerreadingsubmission/capture.html', context)

    @staticmethod
    def _capture_meters(form):
        return form.fields['meter'].queryset

    def _get_visible(self, request, object_id):
        try:
            return self.get_queryset(request).get(pk=object_id)
        except ControllerReadingSubmission.DoesNotExist as error:
            raise Http404 from error

    def photo_view(self, request, object_id):
        submission = self._get_visible(request, object_id)
        response = FileResponse(submission.photo.open('rb'), content_type='application/octet-stream')
        response['Content-Disposition'] = f'inline; filename="meter-{submission.pk}{Path(submission.photo.name).suffix}"'
        response['Cache-Control'] = 'private, no-store'
        return response

    def approve_view(self, request, object_id):
        if request.method != 'POST' or not request.user.has_perm('water.change_controllerreadingsubmission'):
            raise PermissionDenied
        try:
            with transaction.atomic():
                submission = ControllerReadingSubmission.objects.select_for_update().select_related('meter').get(pk=object_id)
                if submission.status != 'pending':
                    raise ValidationError('Запись уже проверена.')
                reading = Reading(meter=submission.meter, date=submission.date, value=submission.value, notes=f'По фото контролёра. {submission.notes}'.strip())
                reading._history_user = request.user
                reading._change_reason = 'Принято из премодерации'
                reading.save()
                submission.status = 'approved'
                submission.reviewed_by = request.user
                submission.reviewed_at = timezone.now()
                submission.reading = reading
                submission._history_user = request.user
                submission._change_reason = 'Показание принято'
                submission.save()
        except (ValidationError, IntegrityError) as error:
            messages.error(request, f'Не удалось принять: {validation_text(error) if isinstance(error, ValidationError) else "за эту дату уже есть показание"}.')
        else:
            messages.success(request, 'Показание принято и добавлено в журнал.')
        return HttpResponseRedirect(reverse('admin:water_controllerreadingsubmission_change', args=[object_id]))

    def reject_view(self, request, object_id):
        if request.method != 'POST' or not request.user.has_perm('water.change_controllerreadingsubmission'):
            raise PermissionDenied
        with transaction.atomic():
            submission = ControllerReadingSubmission.objects.select_for_update().get(pk=object_id)
            if submission.status == 'pending':
                submission.status = 'rejected'
                submission.review_comment = request.POST.get('review_comment', '').strip()[:500]
                submission.reviewed_by = request.user
                submission.reviewed_at = timezone.now()
                submission._history_user = request.user
                submission._change_reason = 'Показание отклонено'
                submission.save()
        messages.success(request, 'Запись отклонена.')
        return HttpResponseRedirect(reverse('admin:water_controllerreadingsubmission_change', args=[object_id]))


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
    actions = ('approve_drafts', 'cancel_drafts')

    @admin.action(description='Утвердить выбранные черновики')
    def approve_drafts(self, request, queryset):
        changed = 0
        for charge in queryset.select_related('period').order_by('id'):
            if charge.status != 'draft':
                continue
            charge.status = 'approved'
            charge._history_user = request.user
            charge._change_reason = 'Утверждение начисления администратором'
            charge.save()
            self.log_change(request, charge, 'Начисление утверждено')
            changed += 1
        messages.success(request, f'Утверждено начислений: {changed}.')

    @admin.action(description='Отменить выбранные черновики')
    def cancel_drafts(self, request, queryset):
        changed = 0
        for charge in queryset.order_by('id'):
            if charge.status != 'draft':
                continue
            charge.status = 'cancelled'
            charge._history_user = request.user
            charge._change_reason = 'Отмена черновика администратором'
            charge.save()
            self.log_change(request, charge, 'Черновик отменён')
            changed += 1
        messages.success(request, f'Отменено черновиков: {changed}.')


@admin.register(Payment)
class PaymentAdmin(RecordedAdmin):
    list_display = ('account', 'paid_on', 'amount', 'method', 'status', 'reference')
    list_filter = ('status', 'method', 'paid_on')
    search_fields = ('account__number', 'account__plot', 'reference', 'notes')
    autocomplete_fields = ('account',)
    actions = ('allocate_confirmed',)

    @admin.action(description='Распределить подтверждённые оплаты по правилам')
    def allocate_confirmed(self, request, queryset):
        from .billing import allocate_payment
        total = 0
        for payment in queryset.order_by('paid_on', 'id'):
            try:
                allocations, message = allocate_payment(payment, actor=request.user)
            except ValidationError as error:
                messages.error(request, f'{payment}: {validation_text(error)}')
                continue
            total += len(allocations)
            if allocations:
                messages.success(request, f'{payment}: {message}')
            else:
                messages.warning(request, f'{payment}: {message}')
        if total:
            messages.success(request, f'Создано распределений: {total}.')


@admin.register(PaymentAllocation)
class PaymentAllocationAdmin(RecordedAdmin):
    list_display = ('payment', 'charge', 'amount')
    search_fields = ('payment__account__number', 'charge__account__number')
    autocomplete_fields = ('payment', 'charge')


@admin.register(ImportBatch)
class ImportBatchAdmin(RecordedAdmin):
    change_list_template = 'admin/water/importbatch/change_list.html'
    list_display = ('filename', 'sheet', 'effective_date', 'status', 'row_count')
    list_filter = ('status', 'effective_date')
    search_fields = ('filename', 'sha256', 'notes')
    readonly_fields = ('filename', 'sha256', 'sheet', 'effective_date', 'status', 'row_count')

    def has_add_permission(self, request):
        return False

    def get_urls(self):
        return [
            path('upload/', self.admin_site.admin_view(self.upload_view), name='water_importbatch_upload'),
        ] + super().get_urls()

    def upload_view(self, request):
        if not request.user.has_perm('water.add_importbatch'):
            raise PermissionDenied
        form = ImportUploadForm(request.POST or None, request.FILES or None, initial={'effective_date': timezone.localdate()})
        if request.method == 'POST' and form.is_valid():
            from .imports import stage_import
            try:
                batch = stage_import(form.cleaned_data['file'], form.cleaned_data['effective_date'], actor=request.user)
            except ValidationError as error:
                form.add_error('file', validation_text(error))
            else:
                batch.notes = form.cleaned_data['notes']
                batch._history_user = request.user
                batch._change_reason = 'Примечание к пакету импорта'
                batch.save()
                messages.success(request, f'Распознано строк: {batch.row_count}. Рабочая база пока не изменена.')
                return HttpResponseRedirect(reverse('admin:water_importrow_changelist') + f'?batch__id__exact={batch.pk}')
        context = {
            **self.admin_site.each_context(request), 'title': 'Предварительная загрузка данных',
            'opts': self.model._meta, 'form': form,
        }
        return TemplateResponse(request, 'admin/water/importbatch/upload.html', context)


@admin.register(ImportRow)
class ImportRowAdmin(RecordedAdmin):
    list_display = ('batch', 'row_number', 'account_number', 'plot_label', 'person_name', 'status', 'issues')
    list_filter = ('status', 'batch')
    search_fields = ('account_number', 'plot_label', 'person_name', 'phone', 'email', 'cadastral_number')
    readonly_fields = ('batch', 'row_number', 'applied_account', 'applied_plot', 'applied_person')
    actions = ('apply_ready', 'mark_skipped')

    @admin.action(description='Применить выбранные проверенные строки')
    def apply_ready(self, request, queryset):
        from .imports import apply_import_row
        applied = 0
        for row in queryset.order_by('batch_id', 'row_number'):
            try:
                apply_import_row(row, actor=request.user)
            except ValidationError as error:
                row.refresh_from_db()
                row.status = 'review'
                row.issues = validation_text(error)
                row._history_user = request.user
                row._change_reason = 'Импорт остановлен для ручной проверки'
                row.save()
            else:
                applied += 1
        messages.success(request, f'Применено строк: {applied}. Остальные оставлены для проверки.')

    @admin.action(description='Пометить выбранные строки как пропущенные')
    def mark_skipped(self, request, queryset):
        changed = 0
        batch_ids = set()
        for row in queryset.exclude(status='applied'):
            batch_ids.add(row.batch_id)
            row.status = 'skipped'
            row._history_user = request.user
            row._change_reason = 'Строка пропущена администратором'
            row.save()
            changed += 1
        for batch in ImportBatch.objects.filter(pk__in=batch_ids):
            remaining = batch.rows.exclude(status__in=('applied', 'skipped')).exists()
            batch.status = 'partial' if remaining else ('applied' if batch.rows.filter(status='applied').exists() else 'rejected')
            batch._history_user = request.user
            batch._change_reason = 'Обновление после пропуска строк'
            batch.save()
        messages.success(request, f'Пропущено строк: {changed}.')


@admin.register(ResidentAccess)
class ResidentAccessAdmin(RecordedAdmin):
    list_display = ('user', 'account', 'role', 'starts', 'ends', 'verified_at')
    list_filter = ('role', 'starts', 'ends')
    search_fields = ('user__username', 'user__email', 'account__number', 'account__plot')
    autocomplete_fields = ('account',)
    readonly_fields = ('password_reset_link',)

    def get_urls(self):
        return [
            path(
                '<path:object_id>/reset-password/', self.admin_site.admin_view(self.reset_password_view),
                name='water_residentaccess_reset_password',
            ),
        ] + super().get_urls()

    @admin.display(description='Восстановление доступа')
    def password_reset_link(self, obj):
        if not obj.pk:
            return 'Появится после сохранения'
        return format_html(
            '<a class="button" href="{}">Создать одноразовую ссылку</a>',
            reverse('admin:water_residentaccess_reset_password', args=[obj.pk]),
        )

    def reset_password_view(self, request, object_id):
        if not request.user.has_perm('water.add_residentpasswordreset'):
            raise PermissionDenied
        access = self.get_object(request, object_id)
        if access is None:
            raise PermissionDenied
        reset_url = None
        error = None
        if request.method == 'POST':
            from .portal import issue_password_reset
            try:
                reset, raw = issue_password_reset(access.user, actor=request.user)
            except ValidationError as validation_error:
                error = validation_text(validation_error)
            else:
                reset_url = request.build_absolute_uri(reverse('resident_password_reset', args=[raw]))
        context = {
            **self.admin_site.each_context(request), 'title': f'Восстановление доступа: {access.user}',
            'opts': self.model._meta, 'access': access, 'reset_url': reset_url, 'error': error,
        }
        return TemplateResponse(request, 'admin/water/residentaccess/reset_password.html', context)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'user':
            kwargs['queryset'] = User.objects.filter(is_staff=False, is_active=True).order_by('email', 'username')
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(ResidentInvite)
class ResidentInviteAdmin(RecordedAdmin):
    list_display = ('email', 'account', 'role', 'expires_at', 'used_at', 'revoked')
    list_filter = ('revoked', 'role', 'expires_at', 'used_at')
    search_fields = ('email', 'account__number', 'account__plot')
    readonly_fields = ('account', 'email', 'role', 'token_hash', 'expires_at', 'used_at')
    actions = ('revoke_invites',)

    def has_add_permission(self, request):
        return False

    @admin.action(description='Отозвать выбранные неиспользованные приглашения')
    def revoke_invites(self, request, queryset):
        changed = 0
        for invite in queryset.filter(used_at__isnull=True, revoked=False):
            invite.revoked = True
            invite._history_user = request.user
            invite._change_reason = 'Приглашение отозвано администратором'
            invite.save()
            changed += 1
        messages.success(request, f'Отозвано приглашений: {changed}.')


@admin.register(ResidentPasswordReset)
class ResidentPasswordResetAdmin(RecordedAdmin):
    list_display = ('user', 'expires_at', 'used_at', 'revoked')
    list_filter = ('revoked', 'expires_at', 'used_at')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('user', 'token_hash', 'expires_at', 'used_at')
    actions = ('revoke_resets',)

    def has_add_permission(self, request):
        return False

    @admin.action(description='Отозвать выбранные ссылки восстановления')
    def revoke_resets(self, request, queryset):
        changed = 0
        for reset in queryset.filter(used_at__isnull=True, revoked=False):
            reset.revoked = True
            reset._history_user = request.user
            reset._change_reason = 'Ссылка восстановления отозвана администратором'
            reset.save()
            changed += 1
        messages.success(request, f'Отозвано ссылок: {changed}.')


@admin.register(AppealCategory)
class AppealCategoryAdmin(RecordedAdmin):
    list_display = ('name', 'active', 'sort_order')
    list_filter = ('active',)
    search_fields = ('name', 'instructions')


@admin.register(ResidentAppeal)
class ResidentAppealAdmin(RecordedAdmin):
    list_display = ('id', 'opened_at', 'account', 'category', 'subject', 'status', 'responded_at')
    list_filter = ('status', 'category', 'opened_at')
    search_fields = ('=id', 'account__number', 'account__plot', 'author__email', 'subject', 'message', 'response')
    readonly_fields = ('account', 'author', 'category', 'subject', 'message', 'opened_at', 'responded_at', 'responded_by')
    autocomplete_fields = ('account',)

    def has_add_permission(self, request):
        return False

    def save_model(self, request, obj, form, change):
        old_response = ResidentAppeal.objects.get(pk=obj.pk).response if obj.pk else ''
        if obj.response.strip() and obj.response != old_response:
            obj.responded_at = timezone.now()
            obj.responded_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ResidentAppealMessage)
class ResidentAppealMessageAdmin(RecordedAdmin):
    list_display = ('created_at', 'appeal', 'author', 'body')
    search_fields = ('appeal__id', 'appeal__subject', 'author__email', 'body')
    readonly_fields = ('appeal', 'author', 'body', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.method in ('GET', 'HEAD', 'OPTIONS') and super().has_change_permission(request, obj)


@admin.register(DocumentCategory)
class DocumentCategoryAdmin(RecordedAdmin):
    list_display = ('name', 'active', 'sort_order')
    list_filter = ('active',)
    search_fields = ('name',)


@admin.register(AccountDocument)
class AccountDocumentAdmin(RecordedAdmin):
    list_display = ('title', 'account', 'category', 'published_at', 'visible_to_residents', 'file_size')
    list_filter = ('visible_to_residents', 'category', 'published_at')
    search_fields = ('title', 'original_name', 'account__number', 'account__plot', 'notes')
    autocomplete_fields = ('account',)

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj)) + ('original_name', 'file_size')
        return fields + (('document',) if obj else ())
