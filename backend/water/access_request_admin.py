from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .access_requests import ResidentAccessRequest
from .models import Account, ResidentAccess
from .portal import issue_invite
from .privacy_admin import PrivateRegistryPermissionMixin


class AccessRequestApproveForm(forms.Form):
    account = forms.ModelChoiceField(
        label='Проверенный лицевой счёт',
        queryset=Account.objects.filter(archived=False).order_by('plot', 'number', 'id'),
    )
    role = forms.ChoiceField(
        label='Основание доступа', choices=ResidentAccess._meta.get_field('role').choices,
    )
    email = forms.EmailField(label='Email для одноразового приглашения')
    decision_note = forms.CharField(
        label='Основание решения', max_length=1000,
        widget=forms.Textarea(attrs={'rows': 4}),
    )


class AccessRequestRejectForm(forms.Form):
    decision_note = forms.CharField(
        label='Причина отклонения', max_length=1000,
        widget=forms.Textarea(attrs={'rows': 4}),
    )


@admin.register(ResidentAccessRequest)
class ResidentAccessRequestAdmin(PrivateRegistryPermissionMixin, admin.ModelAdmin):
    list_display = (
        'submitted_at', 'full_name', 'plot_hint', 'claimed_role', 'status',
        'matched_account', 'decided_at',
    )
    list_filter = ('status', 'claimed_role')
    search_fields = ('full_name', 'email', 'phone', 'plot_hint')
    list_select_related = ('matched_account', 'decided_by', 'invite')
    date_hierarchy = 'submitted_at'
    actions = None
    readonly_fields = (
        'submitted_at', 'full_name', 'email', 'phone', 'plot_hint', 'claimed_role', 'message',
        'status', 'matched_account', 'approved_role', 'decision_note', 'decided_by', 'decided_at',
        'invite', 'decision_actions',
    )
    fields = (
        'submitted_at', 'full_name', 'email', 'phone', 'plot_hint', 'claimed_role', 'message',
        'status', 'matched_account', 'approved_role', 'decision_note', 'decided_by', 'decided_at',
        'invite', 'decision_actions',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [
            path(
                '<path:object_id>/approve/',
                self.admin_site.admin_view(self.approve_view),
                name='water_residentaccessrequest_approve',
            ),
            path(
                '<path:object_id>/reject/',
                self.admin_site.admin_view(self.reject_view),
                name='water_residentaccessrequest_reject',
            ),
        ] + super().get_urls()

    @admin.display(description='Решение')
    def decision_actions(self, obj):
        if not obj or obj.status != ResidentAccessRequest.STATUS_NEW:
            return 'Решение уже зафиксировано.'
        approve = reverse('admin:water_residentaccessrequest_approve', args=[obj.pk])
        reject = reverse('admin:water_residentaccessrequest_reject', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">Проверить и одобрить</a> &nbsp; '
            '<a class="button" href="{}">Отклонить</a>',
            approve, reject,
        )

    def _can_review(self, request, obj):
        return self.has_change_permission(request, obj)

    def approve_view(self, request, object_id):
        obj = get_object_or_404(ResidentAccessRequest, pk=object_id)
        if not self._can_review(request, obj) or not request.user.has_perm('water.add_residentinvite'):
            raise PermissionDenied
        if obj.status != ResidentAccessRequest.STATUS_NEW:
            messages.warning(request, 'По этой заявке решение уже принято.')
            return HttpResponseRedirect(reverse('admin:water_residentaccessrequest_change', args=[obj.pk]))

        form = AccessRequestApproveForm(request.POST or None, initial={
            'email': obj.email,
            'role': 'owner',
        })
        invite_url = None
        if request.method == 'POST' and form.is_valid():
            try:
                with transaction.atomic():
                    locked = ResidentAccessRequest.objects.select_for_update().get(pk=obj.pk)
                    if locked.status != ResidentAccessRequest.STATUS_NEW:
                        raise ValidationError('По этой заявке решение уже принято.')
                    invite, raw = issue_invite(
                        form.cleaned_data['account'],
                        form.cleaned_data['email'],
                        form.cleaned_data['role'],
                        actor=request.user,
                    )
                    locked.status = ResidentAccessRequest.STATUS_APPROVED
                    locked.matched_account = form.cleaned_data['account']
                    locked.approved_role = form.cleaned_data['role']
                    locked.decision_note = form.cleaned_data['decision_note'].strip()
                    locked.decided_by = request.user
                    locked.decided_at = timezone.now()
                    locked.invite = invite
                    locked.save()
                    invite_url = request.build_absolute_uri(reverse('resident_invite', args=[raw]))
                    obj = locked
            except ValidationError as error:
                form.add_error(None, '; '.join(error.messages))

        context = {
            **self.admin_site.each_context(request),
            'title': 'Проверка запроса доступа',
            'opts': self.model._meta,
            'request_obj': obj,
            'form': form,
            'invite_url': invite_url,
        }
        return TemplateResponse(request, 'admin/water/access_request/approve.html', context)

    def reject_view(self, request, object_id):
        obj = get_object_or_404(ResidentAccessRequest, pk=object_id)
        if not self._can_review(request, obj):
            raise PermissionDenied
        if obj.status != ResidentAccessRequest.STATUS_NEW:
            messages.warning(request, 'По этой заявке решение уже принято.')
            return HttpResponseRedirect(reverse('admin:water_residentaccessrequest_change', args=[obj.pk]))

        form = AccessRequestRejectForm(request.POST or None)
        if request.method == 'POST' and form.is_valid():
            try:
                with transaction.atomic():
                    locked = ResidentAccessRequest.objects.select_for_update().get(pk=obj.pk)
                    if locked.status != ResidentAccessRequest.STATUS_NEW:
                        raise ValidationError('По этой заявке решение уже принято.')
                    locked.status = ResidentAccessRequest.STATUS_REJECTED
                    locked.decision_note = form.cleaned_data['decision_note'].strip()
                    locked.decided_by = request.user
                    locked.decided_at = timezone.now()
                    locked.save()
            except ValidationError as error:
                form.add_error(None, '; '.join(error.messages))
            else:
                messages.success(request, 'Заявка отклонена. Решение зафиксировано.')
                return HttpResponseRedirect(reverse('admin:water_residentaccessrequest_change', args=[obj.pk]))

        context = {
            **self.admin_site.each_context(request),
            'title': 'Отклонение запроса доступа',
            'opts': self.model._meta,
            'request_obj': obj,
            'form': form,
        }
        return TemplateResponse(request, 'admin/water/access_request/reject.html', context)
