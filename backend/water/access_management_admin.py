from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .access_workflow import (
    end_resident_access,
    issue_access_password_reset,
    revoke_invite,
    revoke_password_reset,
)
from .admin import RecordedAdmin, validation_text
from .models import ResidentAccess, ResidentInvite, ResidentPasswordReset


class EndResidentAccessAdminForm(forms.Form):
    ends_on = forms.DateField(label='Завершить доступ с', widget=forms.DateInput(attrs={'type': 'date'}))

    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields['ends_on'].initial = timezone.localdate()

    def clean_ends_on(self):
        value = self.cleaned_data['ends_on']
        if self.access and value <= self.access.starts:
            raise forms.ValidationError('Дата завершения должна быть позже даты начала доступа.')
        return value


for model in (ResidentAccess, ResidentInvite, ResidentPasswordReset):
    try:
        admin.site.unregister(model)
    except admin.sites.NotRegistered:
        pass


@admin.register(ResidentAccess)
class ResidentAccessAdmin(RecordedAdmin):
    list_display = ('user', 'account', 'role', 'starts', 'ends', 'verified_at')
    list_filter = ('role', 'starts', 'ends')
    search_fields = ('user__username', 'user__email', 'account__number', 'account__plot')
    autocomplete_fields = ('account',)
    readonly_fields = ('user', 'account', 'role', 'starts', 'ends', 'verified_at', 'end_access_link', 'password_reset_link')

    def has_add_permission(self, request):
        # ResidentAccess is created by invite activation, never manually by staff.
        return False

    def get_urls(self):
        return [
            path(
                '<path:object_id>/end-access/', self.admin_site.admin_view(self.end_access_view),
                name='water_residentaccess_end',
            ),
            path(
                '<path:object_id>/reset-password/', self.admin_site.admin_view(self.reset_password_view),
                name='water_residentaccess_reset_password',
            ),
        ] + super().get_urls()

    @admin.display(description='Завершение доступа')
    def end_access_link(self, obj):
        if not obj.pk:
            return '—'
        if obj.ends:
            return f'Завершён {obj.ends:%d.%m.%Y}'
        return format_html(
            '<a class="button" href="{}">Завершить доступ датой</a>',
            reverse('admin:water_residentaccess_end', args=[obj.pk]),
        )

    @admin.display(description='Восстановление доступа')
    def password_reset_link(self, obj):
        if not obj.pk:
            return '—'
        return format_html(
            '<a class="button" href="{}">Создать одноразовую ссылку</a>',
            reverse('admin:water_residentaccess_reset_password', args=[obj.pk]),
        )

    def end_access_view(self, request, object_id):
        if not request.user.has_perm('water.change_residentaccess'):
            raise PermissionDenied
        access = self.get_object(request, object_id)
        if access is None:
            raise PermissionDenied
        form = EndResidentAccessAdminForm(request.POST or None, access=access)
        if request.method == 'POST' and form.is_valid():
            try:
                end_resident_access(access.pk, ends_on=form.cleaned_data['ends_on'], actor=request.user)
            except ValidationError as error:
                form.add_error(None, validation_text(error))
            else:
                messages.success(request, 'Доступ завершён датой; история записи сохранена.')
                return HttpResponseRedirect(reverse('admin:water_residentaccess_change', args=[access.pk]))
        context = {
            **self.admin_site.each_context(request),
            'title': f'Завершить доступ: {access}',
            'opts': self.model._meta,
            'access': access,
            'form': form,
        }
        return TemplateResponse(request, 'admin/water/resident_access/end.html', context)

    def reset_password_view(self, request, object_id):
        if not request.user.has_perm('water.add_residentpasswordreset'):
            raise PermissionDenied
        access = self.get_object(request, object_id)
        if access is None:
            raise PermissionDenied
        reset_url = None
        error = None
        if request.method == 'POST':
            try:
                _reset, raw = issue_access_password_reset(access.pk, actor=request.user)
            except ValidationError as validation_error:
                error = validation_text(validation_error)
            else:
                reset_url = request.build_absolute_uri(reverse('resident_password_reset', args=[raw]))
        context = {
            **self.admin_site.each_context(request),
            'title': f'Восстановление доступа: {access.user}',
            'opts': self.model._meta,
            'access': access,
            'reset_url': reset_url,
            'error': error,
        }
        return TemplateResponse(request, 'admin/water/residentaccess/reset_password.html', context)


@admin.register(ResidentInvite)
class ResidentInviteAdmin(RecordedAdmin):
    list_display = ('email', 'account', 'role', 'expires_at', 'used_at', 'revoked')
    list_filter = ('revoked', 'role', 'expires_at', 'used_at')
    search_fields = ('email', 'account__number', 'account__plot')
    readonly_fields = ('account', 'email', 'role', 'token_hash', 'expires_at', 'used_at', 'revoked')
    actions = ('revoke_invites',)

    def has_add_permission(self, request):
        return False

    @admin.action(description='Отозвать выбранные неиспользованные приглашения')
    def revoke_invites(self, request, queryset):
        changed = 0
        blocked = 0
        for invite in queryset:
            try:
                revoke_invite(invite.pk, actor=request.user)
            except ValidationError:
                blocked += 1
            else:
                changed += 1
        if changed:
            messages.success(request, f'Отозвано приглашений: {changed}.')
        if blocked:
            messages.warning(request, f'Не изменено приглашений: {blocked}; они уже использованы или отозваны.')


@admin.register(ResidentPasswordReset)
class ResidentPasswordResetAdmin(RecordedAdmin):
    list_display = ('user', 'expires_at', 'used_at', 'revoked')
    list_filter = ('revoked', 'expires_at', 'used_at')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('user', 'token_hash', 'expires_at', 'used_at', 'revoked')
    actions = ('revoke_resets',)

    def has_add_permission(self, request):
        return False

    @admin.action(description='Отозвать выбранные ссылки восстановления')
    def revoke_resets(self, request, queryset):
        changed = 0
        blocked = 0
        for reset in queryset:
            try:
                revoke_password_reset(reset.pk, actor=request.user)
            except ValidationError:
                blocked += 1
            else:
                changed += 1
        if changed:
            messages.success(request, f'Отозвано ссылок восстановления: {changed}.')
        if blocked:
            messages.warning(request, f'Не изменено ссылок: {blocked}; они уже использованы или отозваны.')
