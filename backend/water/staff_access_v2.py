from datetime import date

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone

from .access_control import (
    AccessAssignment,
    AccessDelegation,
    active_assignments,
    assignment_from_role,
    create_delegation,
    link_identity,
    revoke_assignment,
    revoke_delegation,
    transfer_identity,
    update_assignment_capabilities,
)
from .access_policy import CAPABILITIES, ROLE_TEMPLATES, ScopeType
from .access_resolver import can
from .access_scope import ScopeRef, scoped_accounts
from .board_polls import BoardMembership
from .controller_scope import ControllerLineAccess
from .models import Account, LandPlot, Person, PlotRelation, ResidentAccess, SupplyNode, User, WaterGroup
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity, TsnMembership
from .staff_workspace import _base_context


ALL_SCOPE = ScopeRef(ScopeType.ALL)
SERVICE_ROLE_CHOICES = [
    (code, template.label)
    for code, template in ROLE_TEMPLATES.items()
    if code != 'resident_account'
]
DELEGABLE_CHOICES = [
    (code, spec.label) for code, spec in CAPABILITIES.items() if spec.delegable
]


class IdentityLinkForm(forms.Form):
    user = forms.ModelChoiceField(label='Учётная запись', queryset=User.objects.none())
    basis = forms.CharField(label='Основание подтверждения / смены логина', max_length=300)

    def __init__(self, *args, person=None, **kwargs):
        super().__init__(*args, **kwargs)
        occupied = ResidentIdentity.objects.exclude(person=person).values('user_id')
        self.fields['user'].queryset = User.objects.filter(is_active=True).exclude(pk__in=occupied).order_by(
            'last_name', 'first_name', 'username', 'id'
        )


class AssignmentForm(forms.Form):
    role_code = forms.ChoiceField(label='Роль / назначение', choices=SERVICE_ROLE_CHOICES)
    scope_type = forms.ChoiceField(label='Где действует', choices=[
        (ScopeType.WATER_GROUP.value, 'Конкретная линия'),
        (ScopeType.SUPPLY_NODE.value, 'Конкретный общий узел'),
        (ScopeType.ACCOUNT.value, 'Конкретный лицевой счёт'),
        (ScopeType.ALL.value, 'Весь разрешённый контур'),
    ])
    water_group = forms.ModelChoiceField(label='Линия', queryset=WaterGroup.objects.all(), required=False)
    supply_node = forms.ModelChoiceField(label='Общий узел', queryset=SupplyNode.objects.all(), required=False)
    account = forms.ModelChoiceField(
        label='Лицевой счёт', queryset=Account.objects.filter(archived=False), required=False,
    )
    starts = forms.DateField(label='С', initial=timezone.localdate, widget=forms.DateInput(attrs={'type': 'date'}))
    ends = forms.DateField(label='До (не включая)', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    basis = forms.CharField(label='Основание', max_length=300)
    notes = forms.CharField(label='Примечание', required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def clean(self):
        data = super().clean()
        role_code = data.get('role_code')
        raw_scope = data.get('scope_type')
        if not role_code or not raw_scope:
            return data
        template = ROLE_TEMPLATES[role_code]
        scope = ScopeType(raw_scope)
        if scope not in template.scopes:
            self.add_error('scope_type', f'Для роли «{template.label}» эта область не разрешена.')
            return data
        object_map = {
            ScopeType.WATER_GROUP: data.get('water_group'),
            ScopeType.SUPPLY_NODE: data.get('supply_node'),
            ScopeType.ACCOUNT: data.get('account'),
        }
        selected = object_map.get(scope)
        if scope != ScopeType.ALL and selected is None:
            field = {
                ScopeType.WATER_GROUP: 'water_group',
                ScopeType.SUPPLY_NODE: 'supply_node',
                ScopeType.ACCOUNT: 'account',
            }[scope]
            self.add_error(field, 'Выберите объект области действия.')
        data['scope_object_id'] = selected.pk if selected is not None else None
        starts, ends = data.get('starts'), data.get('ends')
        if starts and ends and ends <= starts:
            self.add_error('ends', 'Дата окончания должна быть позже даты начала.')
        return data


class DelegationForm(forms.Form):
    delegate = forms.ModelChoiceField(label='Кому передать', queryset=Person.objects.none())
    account = forms.ModelChoiceField(label='Лицевой счёт', queryset=Account.objects.none())
    capabilities = forms.MultipleChoiceField(
        label='Что передать', choices=DELEGABLE_CHOICES,
        widget=forms.CheckboxSelectMultiple,
    )
    starts = forms.DateField(label='С', initial=timezone.localdate, widget=forms.DateInput(attrs={'type': 'date'}))
    ends = forms.DateField(label='До (не включая)', required=False, widget=forms.DateInput(attrs={'type': 'date'}))
    basis = forms.CharField(label='Основание передачи', max_length=300)
    notes = forms.CharField(label='Примечание', required=False, widget=forms.Textarea(attrs={'rows': 2}))

    def __init__(self, *args, delegator=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['delegate'].queryset = Person.objects.filter(archived=False).exclude(pk=delegator.pk).order_by('full_name', 'id')
        account_ids = PortalGrant.objects.filter(person=delegator).values('account_id')
        self.fields['account'].queryset = Account.objects.filter(pk__in=account_ids, archived=False).order_by('plot', 'number', 'id')

    def clean(self):
        data = super().clean()
        starts, ends = data.get('starts'), data.get('ends')
        if starts and ends and ends <= starts:
            self.add_error('ends', 'Дата окончания должна быть позже даты начала.')
        return data


class RevokeForm(forms.Form):
    reason = forms.CharField(label='Причина', max_length=300)


def _legacy_manager(user):
    return bool(
        user.is_staff
        and user.has_perm('water.access_private_registry')
        and user.has_perm('water.add_residentinvite')
        and user.has_perm('water.view_portalgrant')
    )


def _can_view_center(user):
    return user.is_superuser or _legacy_manager(user) or can(user, 'access.view', scope=ALL_SCOPE)


def _can_manage_identity(user):
    return user.is_superuser or _legacy_manager(user) or can(user, 'access.identity.verify', scope=ALL_SCOPE)


def _can_manage_assignments(user):
    return user.is_superuser or _legacy_manager(user) or can(user, 'access.assignment.manage', scope=ALL_SCOPE)


def _can_review_delegations(user):
    return user.is_superuser or _legacy_manager(user) or can(user, 'access.delegation.review', scope=ALL_SCOPE)


def _can_view_contacts(user):
    return user.is_superuser or user.has_perm('water.access_private_registry') or can(user, 'registry.contacts.view', scope=ALL_SCOPE)


def _require_center(request, *, manage=False):
    allowed = _can_manage_assignments(request.user) if manage else _can_view_center(request.user)
    if not request.user.is_staff or not allowed:
        raise PermissionDenied


def _is_active(starts, ends, today):
    return starts <= today and (ends is None or ends > today)


def _person_identity(person):
    return ResidentIdentity.objects.select_related('user').filter(person=person).first()


def _role_tags(person, today):
    tags = []
    grants = getattr(person, 'active_portal_grants', [])
    if grants:
        tags.append('Житель')
    for assignment in getattr(person, 'active_v2_assignments', []):
        if assignment.role_label not in tags:
            tags.append(assignment.role_label)
    if any(_is_active(x.starts, x.ends, today) for x in getattr(person, 'current_tsn_memberships', [])):
        tags.append('Член ТСН')
    if any(_is_active(x.starts, x.ends, today) for x in getattr(person, 'current_board_memberships', [])):
        tags.append('Правление')
    return tags


def people_index(request):
    _require_center(request)
    today = timezone.localdate()
    q = ' '.join((request.GET.get('q') or '').split())[:160]
    active_q = Q(starts__lte=today) & (Q(ends__isnull=True) | Q(ends__gt=today))
    people = Person.objects.filter(archived=False).select_related('resident_identity__user').prefetch_related(
        Prefetch('access_assignments', queryset=AccessAssignment.objects.filter(active_q, revoked_at__isnull=True), to_attr='active_v2_assignments'),
        Prefetch('portal_grants', queryset=PortalGrant.objects.filter(active_q), to_attr='active_portal_grants'),
        Prefetch('tsn_memberships', queryset=TsnMembership.objects.all(), to_attr='current_tsn_memberships'),
        Prefetch('board_memberships', queryset=BoardMembership.objects.all(), to_attr='current_board_memberships'),
    )
    if q:
        criteria = Q(full_name__icontains=q)
        if _can_view_contacts(request.user):
            criteria |= Q(email__icontains=q) | Q(phone__icontains=q)
        people = people.filter(criteria)
    people = list(people.order_by('full_name', 'id')[:150])
    rows = [
        {'person': person, 'identity': getattr(person, 'resident_identity', None), 'tags': _role_tags(person, today)}
        for person in people
    ]
    context = _base_context(request, section='access')
    context.update({
        'q': q,
        'rows': rows,
        'can_manage_assignments': _can_manage_assignments(request.user),
        'can_view_contacts': _can_view_contacts(request.user),
        'can_edit_person': _can_view_contacts(request.user) and can(
            request.user, 'registry.edit', scope=ScopeRef(ScopeType.PERSON, person.pk)
        ),
    })
    return TemplateResponse(request, 'water/work/access/people.html', context)


def _scope_label(assignment):
    scope = ScopeType(assignment.scope_type)
    if scope == ScopeType.ALL:
        return 'Весь разрешённый контур'
    model = {
        ScopeType.ACCOUNT: Account,
        ScopeType.WATER_GROUP: WaterGroup,
        ScopeType.SUPPLY_NODE: SupplyNode,
        ScopeType.LAND_PLOT: LandPlot,
        ScopeType.PERSON: Person,
    }.get(scope)
    if model is None:
        return scope.value
    obj = model.objects.filter(pk=assignment.scope_object_id).first()
    return str(obj) if obj else f'{scope.value} #{assignment.scope_object_id}'


def _assignment_rows(person, today):
    rows = []
    for assignment in person.access_assignments.select_related('granted_by', 'revoked_by').order_by('-starts', '-id'):
        row = {
            'assignment': assignment,
            'active': assignment.is_active_on(today),
            'scope_label': _scope_label(assignment),
            'capability_labels': [CAPABILITIES[code].label for code in assignment.capabilities if code in CAPABILITIES],
            'allowed_capabilities': [
                {'code': code, 'label': CAPABILITIES[code].label, 'enabled': code in assignment.capabilities}
                for code in (assignment.allowed_capabilities or assignment.capabilities) if code in CAPABILITIES
            ],
            'accounts': [],
            'account_count': 0,
        }
        if assignment.scope_type in {ScopeType.WATER_GROUP.value, ScopeType.SUPPLY_NODE.value}:
            ref = ScopeRef(ScopeType(assignment.scope_type), assignment.scope_object_id)
            qs = scoped_accounts(ref, today).order_by('plot', 'number', 'id')
            row['account_count'] = qs.count()
            row['accounts'] = list(qs[:50])
        rows.append(row)
    return rows


def _board_memberships_for(person, identity):
    query = Q(person=person)
    if identity:
        query |= Q(user=identity.user)
    return BoardMembership.objects.filter(query).select_related('user', 'person').distinct().order_by('-starts', '-id')


def _legacy_line_accesses(identity):
    if identity is None:
        return ControllerLineAccess.objects.none()
    return ControllerLineAccess.objects.filter(user=identity.user).select_related('group', 'group__node').order_by('-starts', 'group__name')


def person_detail(request, person_id):
    _require_center(request)
    person = get_object_or_404(Person, pk=person_id)
    today = timezone.localdate()
    identity = _person_identity(person)
    action = request.POST.get('action') if request.method == 'POST' else ''

    identity_form = IdentityLinkForm(request.POST if action == 'identity' else None, person=person, prefix='identity')
    assignment_form = AssignmentForm(request.POST if action == 'assign' else None, prefix='assign')
    delegation_form = DelegationForm(request.POST if action == 'delegate' else None, delegator=person, prefix='delegate')
    revoke_form = RevokeForm(request.POST if action in {'revoke_assignment', 'revoke_delegation'} else None, prefix='revoke')

    if request.method == 'POST':
        if action == 'identity':
            if not _can_manage_identity(request.user):
                raise PermissionDenied
            if identity_form.is_valid():
                try:
                    selected = identity_form.cleaned_data['user']
                    if identity and identity.user_id != selected.pk:
                        transfer_identity(identity=identity, new_user=selected, actor=request.user, basis=identity_form.cleaned_data['basis'])
                    elif identity is None:
                        link_identity(user=selected, person=person, actor=request.user, basis=identity_form.cleaned_data['basis'])
                    messages.success(request, 'Учётная запись связана с Person; бизнес-полномочия не создавались автоматически.')
                    return HttpResponseRedirect(reverse('staff_workspace:access_person', args=[person.pk]))
                except ValidationError as error:
                    identity_form.add_error(None, '; '.join(error.messages))

        elif action == 'assign':
            if not _can_manage_assignments(request.user):
                raise PermissionDenied
            if assignment_form.is_valid():
                data = assignment_form.cleaned_data
                try:
                    assignment_from_role(
                        person=person,
                        role_code=data['role_code'],
                        scope_type=data['scope_type'],
                        scope_object_id=data['scope_object_id'],
                        starts=data['starts'], ends=data.get('ends'),
                        basis=data['basis'], granted_by=request.user, notes=data.get('notes', ''),
                    )
                    messages.success(request, 'Назначение выдано. Область действия и снимок прав зафиксированы.')
                    return HttpResponseRedirect(reverse('staff_workspace:access_person', args=[person.pk]))
                except ValidationError as error:
                    assignment_form.add_error(None, '; '.join(error.messages))

        elif action == 'delegate':
            if not _can_review_delegations(request.user):
                raise PermissionDenied
            if delegation_form.is_valid():
                data = delegation_form.cleaned_data
                try:
                    create_delegation(
                        delegator=person, delegate=data['delegate'], capabilities=data['capabilities'],
                        account_id=data['account'].pk, starts=data['starts'], ends=data.get('ends'),
                        basis=data['basis'], verified_by=request.user, notes=data.get('notes', ''),
                    )
                    messages.success(request, 'Делегирование подтверждено. Переданы только выбранные прямые личные права.')
                    return HttpResponseRedirect(reverse('staff_workspace:access_person', args=[person.pk]))
                except ValidationError as error:
                    delegation_form.add_error(None, '; '.join(error.messages))

        elif action == 'edit_assignment':
            if not _can_manage_assignments(request.user):
                raise PermissionDenied
            assignment = get_object_or_404(AccessAssignment, pk=request.POST.get('assignment_id'), person=person)
            try:
                update_assignment_capabilities(
                    assignment, actor=request.user,
                    capabilities=request.POST.getlist('capabilities'),
                    reason=request.POST.get('reason', ''),
                )
                messages.success(request, 'Активный набор прав роли изменён внутри исходного конверта.')
                return HttpResponseRedirect(reverse('staff_workspace:access_person', args=[person.pk]))
            except ValidationError as error:
                messages.error(request, '; '.join(error.messages))

        elif action == 'revoke_assignment':
            if not _can_manage_assignments(request.user):
                raise PermissionDenied
            assignment = get_object_or_404(AccessAssignment, pk=request.POST.get('assignment_id'), person=person)
            if revoke_form.is_valid():
                try:
                    revoke_assignment(assignment, actor=request.user, reason=revoke_form.cleaned_data['reason'])
                    messages.success(request, 'Назначение завершено; история сохранена.')
                    return HttpResponseRedirect(reverse('staff_workspace:access_person', args=[person.pk]))
                except ValidationError as error:
                    revoke_form.add_error(None, '; '.join(error.messages))

        elif action == 'revoke_delegation':
            if not _can_review_delegations(request.user):
                raise PermissionDenied
            delegation = get_object_or_404(AccessDelegation, pk=request.POST.get('delegation_id'), delegator=person)
            if revoke_form.is_valid():
                try:
                    revoke_delegation(delegation, actor=request.user, reason=revoke_form.cleaned_data['reason'])
                    messages.success(request, 'Делегирование отозвано; история сохранена.')
                    return HttpResponseRedirect(reverse('staff_workspace:access_person', args=[person.pk]))
                except ValidationError as error:
                    revoke_form.add_error(None, '; '.join(error.messages))
        else:
            raise PermissionDenied

    grants = person.portal_grants.select_related('account', 'verified_by').order_by('-starts', '-id')
    plot_relations = person.plot_relations.select_related('plot', 'plot__account').order_by('-starts', '-id')
    board_memberships = _board_memberships_for(person, identity)
    incoming = person.incoming_access_delegations.select_related('delegator', 'verified_by').order_by('-starts', '-id')
    outgoing = person.outgoing_access_delegations.select_related('delegate', 'verified_by').order_by('-starts', '-id')
    context = _base_context(request, section='access')
    context.update({
        'person': person,
        'identity': identity,
        'identity_form': identity_form,
        'assignment_form': assignment_form,
        'delegation_form': delegation_form,
        'revoke_form': revoke_form,
        'assignment_rows': _assignment_rows(person, today),
        'grants': list(grants),
        'plot_relations': list(plot_relations),
        'tsn_memberships': list(person.tsn_memberships.order_by('-starts', '-id')),
        'board_memberships': list(board_memberships),
        'incoming_delegations': list(incoming),
        'outgoing_delegations': list(outgoing),
        'legacy_accesses': list(ResidentAccess.objects.filter(user=identity.user).select_related('account').order_by('-starts', '-id')) if identity else [],
        'legacy_line_accesses': list(_legacy_line_accesses(identity)),
        'today': today,
        'can_manage_identity': _can_manage_identity(request.user),
        'can_manage_assignments': _can_manage_assignments(request.user),
        'can_review_delegations': _can_review_delegations(request.user),
        'can_view_contacts': _can_view_contacts(request.user),
    })
    return TemplateResponse(request, 'water/work/access/person_detail.html', context)


workspace_access_people = admin.site.admin_view(people_index)
workspace_access_person = admin.site.admin_view(person_detail)
