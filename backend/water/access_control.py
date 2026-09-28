from datetime import date

from datetime import date

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from .access_policy import CAPABILITIES, ROLE_TEMPLATES, ScopeType
from .models import Person, RecordedModel, User


SCOPE_CHOICES = [(scope.value, scope.value) for scope in ScopeType]
OBJECT_SCOPES = {
    ScopeType.ACCOUNT,
    ScopeType.LAND_PLOT,
    ScopeType.WATER_GROUP,
    ScopeType.SUPPLY_NODE,
    ScopeType.PERSON,
}


def _active_period(queryset, on_date):
    return queryset.filter(starts__lte=on_date).filter(
        Q(ends__isnull=True) | Q(ends__gt=on_date)
    )


def _normalize_capabilities(value):
    if not isinstance(value, list):
        raise ValidationError({'capabilities': 'Права должны быть списком capability-кодов.'})
    cleaned = [str(item).strip() for item in value if str(item).strip()]
    if len(cleaned) != len(set(cleaned)):
        raise ValidationError({'capabilities': 'Один capability не должен повторяться.'})
    unknown = sorted(set(cleaned) - set(CAPABILITIES))
    if unknown:
        raise ValidationError({'capabilities': f'Неизвестные capability: {", ".join(unknown)}'})
    return cleaned


def _scope_type(value):
    try:
        return ScopeType(value)
    except ValueError as exc:
        raise ValidationError({'scope_type': 'Неизвестная область действия.'}) from exc


def _validate_scope(scope_type, scope_object_id):
    scope = _scope_type(scope_type)
    if scope in OBJECT_SCOPES and not scope_object_id:
        raise ValidationError({'scope_object_id': 'Для этой области нужно выбрать объект.'})
    if scope not in OBJECT_SCOPES and scope_object_id is not None:
        raise ValidationError({'scope_object_id': 'Для этой области объект не указывается.'})
    return scope


def _validate_capability_scope(capabilities, scope):
    incompatible = [
        code for code in capabilities if scope not in CAPABILITIES[code].scopes
    ]
    if incompatible:
        raise ValidationError({
            'capabilities': f'Эти права нельзя выдать в области {scope.value}: {", ".join(incompatible)}'
        })

class AccessAssignment(RecordedModel):
    """Person-centric, date-bounded service authority with a frozen capability snapshot."""

    person = models.ForeignKey(
        Person, verbose_name='Человек', on_delete=models.PROTECT,
        related_name='access_assignments',
    )
    role_code = models.CharField('Шаблон роли', max_length=80)
    role_version = models.PositiveIntegerField('Версия шаблона', default=1)
    role_label = models.CharField('Название роли при выдаче', max_length=160)
    allowed_capabilities = models.JSONField('Максимальный набор прав при выдаче', default=list)
    capabilities = models.JSONField('Активный набор прав', default=list)
    scope_type = models.CharField('Область', max_length=24, choices=SCOPE_CHOICES)
    scope_object_id = models.PositiveBigIntegerField('ID объекта области', blank=True, null=True)
    starts = models.DateField('Полномочие с', default=timezone.localdate)
    ends = models.DateField('Полномочие до (не включая)', blank=True, null=True)
    basis = models.CharField('Основание', max_length=300)
    granted_by = models.ForeignKey(
        User, verbose_name='Кто выдал', on_delete=models.PROTECT,
        related_name='issued_access_assignments',
    )
    revoked_at = models.DateTimeField('Отозвано', blank=True, null=True)
    revoked_by = models.ForeignKey(
        User, verbose_name='Кто отозвал', on_delete=models.PROTECT,
        related_name='revoked_access_assignments', blank=True, null=True,
    )
    revocation_reason = models.CharField('Причина отзыва', max_length=300, blank=True)
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Назначение полномочий V2'
        verbose_name_plural = 'Назначения полномочий V2'
        ordering = ['person', 'role_code', '-starts', 'id']
        constraints = [
            models.CheckConstraint(
                condition=Q(ends__isnull=True) | Q(ends__gt=models.F('starts')),
                name='access_assignment_dates',
            ),
        ]

    def clean(self):
        if self.person_id and self.person.archived:
            raise ValidationError({'person': 'Нельзя выдавать полномочия архивной карточке человека.'})
        if self.granted_by_id and not (self.granted_by.is_staff or self.granted_by.is_superuser):
            raise ValidationError({'granted_by': 'Выдать служебное полномочие может только сотрудник.'})
        if self.ends and self.starts and self.ends <= self.starts:
            raise ValidationError({'ends': 'Дата окончания должна быть позже даты начала.'})
        capabilities = _normalize_capabilities(self.capabilities)
        if not capabilities:
            raise ValidationError({'capabilities': 'У назначения должно быть хотя бы одно активное право; для полного выключения завершите назначение.'})
        allowed = _normalize_capabilities(self.allowed_capabilities or capabilities)
        if not set(capabilities) <= set(allowed):
            raise ValidationError({'capabilities': 'Активные права не могут выходить за конверт, зафиксированный при выдаче роли.'})
        scope = _validate_scope(self.scope_type, self.scope_object_id)
        _validate_capability_scope(allowed, scope)
        self.allowed_capabilities = allowed
        self.capabilities = capabilities
        if not self.person_id or not self.starts or not self.role_code:
            return
        overlaps = AccessAssignment.objects.filter(
            person_id=self.person_id, revoked_at__isnull=True,
            role_code=self.role_code,
            scope_type=self.scope_type,
            scope_object_id=self.scope_object_id,
            starts__lt=self.ends or date.max,
        ).filter(Q(ends__isnull=True) | Q(ends__gt=self.starts)).exclude(pk=self.pk)
        if overlaps.exists():
            raise ValidationError('У человека уже есть такое назначение в пересекающийся период.')

    def __str__(self):
        return f'{self.person} · {self.role_label} · {self.scope_type}:{self.scope_object_id or "—"}'

    def is_active_on(self, on_date):
        return not self.revoked_at and self.starts <= on_date and (self.ends is None or self.ends > on_date)


@transaction.atomic
def assignment_from_role(*, person, role_code, scope_type, scope_object_id=None,
                         starts=None, ends=None, basis, granted_by, notes=''):
    template = ROLE_TEMPLATES[role_code]
    scope = ScopeType(scope_type)
    if scope not in template.scopes:
        raise ValidationError({'scope_type': f'Роль «{template.label}» нельзя назначить в этой области.'})
    assignment = AccessAssignment(
        person=person,
        role_code=template.code,
        role_version=template.version,
        role_label=template.label,
        allowed_capabilities=list(template.capabilities),
        capabilities=list(template.capabilities),
        scope_type=scope.value,
        scope_object_id=scope_object_id,
        starts=starts or timezone.localdate(),
        ends=ends,
        basis=basis,
        granted_by=granted_by,
        notes=notes,
    )
    assignment._history_user = granted_by
    assignment._change_reason = f'Выдана роль V2: {template.label}'
    assignment.save()
    sync_service_gateway(person.pk)
    return assignment


class AccessDelegation(RecordedModel):
    """A limited transfer of delegable personal authority from one Person to another."""

    delegator = models.ForeignKey(
        Person, verbose_name='Кто передаёт', on_delete=models.PROTECT,
        related_name='outgoing_access_delegations',
    )
    delegate = models.ForeignKey(
        Person, verbose_name='Кому передаёт', on_delete=models.PROTECT,
        related_name='incoming_access_delegations',
    )
    capabilities = models.JSONField('Переданные права', default=list)
    scope_type = models.CharField('Область', max_length=24, choices=SCOPE_CHOICES, default=ScopeType.ACCOUNT.value)
    scope_object_id = models.PositiveBigIntegerField('ID объекта области')
    starts = models.DateField('Действует с', default=timezone.localdate)
    ends = models.DateField('Действует до (не включая)', blank=True, null=True)
    basis = models.CharField('Основание передачи', max_length=300)
    verified_by = models.ForeignKey(
        User, verbose_name='Кто подтвердил', on_delete=models.PROTECT,
        related_name='verified_access_delegations',
    )
    revoked_at = models.DateTimeField('Отозвано', blank=True, null=True)
    revoked_by = models.ForeignKey(
        User, verbose_name='Кто отозвал', on_delete=models.PROTECT,
        related_name='revoked_access_delegations', blank=True, null=True,
    )
    revocation_reason = models.CharField('Причина отзыва', max_length=300, blank=True)
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Делегирование полномочий V2'
        verbose_name_plural = 'Делегирования полномочий V2'
        ordering = ['delegator', 'delegate', '-starts', 'id']
        constraints = [
            models.CheckConstraint(
                condition=Q(ends__isnull=True) | Q(ends__gt=models.F('starts')),
                name='access_delegation_dates',
            ),
            models.CheckConstraint(
                condition=~Q(delegator=models.F('delegate')),
                name='access_delegation_different_people',
            ),
        ]

    def is_active_on(self, on_date):
        return (
            not self.revoked_at
            and self.starts <= on_date
            and (self.ends is None or self.ends > on_date)
        )

    def clean(self):
        if self.delegator_id and self.delegator.archived:
            raise ValidationError({'delegator': 'Архивный человек не может передавать полномочия.'})
        if self.delegate_id and self.delegate.archived:
            raise ValidationError({'delegate': 'Нельзя передать полномочия архивному человеку.'})
        if self.delegator_id and self.delegate_id and self.delegator_id == self.delegate_id:
            raise ValidationError({'delegate': 'Нельзя передать полномочие самому себе.'})
        if self.verified_by_id and not (self.verified_by.is_staff or self.verified_by.is_superuser):
            raise ValidationError({'verified_by': 'Делегирование должен подтвердить сотрудник.'})
        if self.ends and self.starts and self.ends <= self.starts:
            raise ValidationError({'ends': 'Дата окончания должна быть позже даты начала.'})
        capabilities = _normalize_capabilities(self.capabilities)
        if not capabilities:
            raise ValidationError({'capabilities': 'Нужно передать хотя бы одно право.'})
        not_delegable = [code for code in capabilities if not CAPABILITIES[code].delegable]
        if not_delegable:
            raise ValidationError({
                'capabilities': f'Эти права нельзя делегировать: {", ".join(not_delegable)}'
            })
        if any(code != 'resident.account.view' for code in capabilities) and 'resident.account.view' not in capabilities:
            raise ValidationError({
                'capabilities': 'Специальные личные права можно передать только вместе с базовым доступом к счёту.'
            })
        scope = _validate_scope(self.scope_type, self.scope_object_id)
        if scope != ScopeType.ACCOUNT:
            raise ValidationError({'scope_type': 'На первом этапе делегируются только личные права конкретного счёта.'})
        _validate_capability_scope(capabilities, scope)
        self.capabilities = capabilities
        if not self.delegator_id or not self.delegate_id or not self.starts:
            return
        if not direct_person_authority_covers(
            self.delegator_id,
            capabilities,
            scope,
            self.scope_object_id,
            self.starts,
            self.ends,
        ):
            raise ValidationError('Нельзя передать больше прав или более долгий срок, чем есть у передающего.')

    def __str__(self):
        return f'{self.delegator} → {self.delegate} · {self.scope_type}:{self.scope_object_id}'


def _covers_period(starts, ends, wanted_starts, wanted_ends):
    if starts > wanted_starts:
        return False
    if wanted_ends is None:
        return ends is None
    return ends is None or ends >= wanted_ends

def direct_person_authority_covers(person_id, capabilities, scope, object_id, starts, ends):
    """Only direct authority counts; delegated authority can never be re-delegated."""
    if scope != ScopeType.ACCOUNT:
        return False
    wanted = set(capabilities)
    available = set()

    assignments = AccessAssignment.objects.filter(
        person_id=person_id, revoked_at__isnull=True,
        scope_type=scope.value,
        scope_object_id=object_id,
    )
    for assignment in assignments:
        if _covers_period(assignment.starts, assignment.ends, starts, ends):
            available.update(set(assignment.capabilities) & wanted)

    from .portal_permissions import PortalGrant
    grants = PortalGrant.objects.filter(person_id=person_id, account_id=object_id)
    portal_map = {
        'resident.account.view': 'can_view_account',
        'resident.finance.view': 'can_view_finance',
        'resident.water.submit': 'can_submit_water',
        'resident.documents.view': 'can_view_documents',
        'resident.appeals.use': 'can_use_appeals',
        'resident.represent': 'can_represent',
    }
    for grant in grants:
        if not _covers_period(grant.starts, grant.ends, starts, ends):
            continue
        for code, field in portal_map.items():
            if code in wanted and getattr(grant, field):
                available.add(code)
    return wanted <= available


def active_assignments(person_id, on_date=None):
    on_date = on_date or timezone.localdate()
    return _active_period(
        AccessAssignment.objects.filter(person_id=person_id, revoked_at__isnull=True), on_date
    )


def active_delegations(delegate_id, on_date=None):
    on_date = on_date or timezone.localdate()
    return _active_period(
        AccessDelegation.objects.filter(delegate_id=delegate_id, revoked_at__isnull=True), on_date
    )


@transaction.atomic
def create_delegation(*, delegator, delegate, capabilities, account_id,
                      starts=None, ends=None, basis, verified_by, notes=''):
    delegation = AccessDelegation(
        delegator=delegator,
        delegate=delegate,
        capabilities=list(capabilities),
        scope_type=ScopeType.ACCOUNT.value,
        scope_object_id=account_id,
        starts=starts or timezone.localdate(),
        ends=ends,
        basis=basis,
        verified_by=verified_by,
        notes=notes,
    )
    delegation._history_user = verified_by
    delegation._change_reason = 'Подтверждено делегирование полномочий V2'
    delegation.save()
    return delegation


def direct_person_has_authority_on(person_id, capability, scope_type, scope_object_id, on_date):
    """Point-in-time direct authority check; intentionally excludes delegations."""
    assignments = active_assignments(person_id, on_date).filter(
        scope_type=scope_type.value,
        scope_object_id=scope_object_id,
    )
    if any(capability in row.capabilities for row in assignments):
        return True
    if scope_type != ScopeType.ACCOUNT:
        return False
    from .portal_permissions import PortalGrant
    field = {
        'resident.account.view': 'can_view_account',
        'resident.finance.view': 'can_view_finance',
        'resident.water.submit': 'can_submit_water',
        'resident.documents.view': 'can_view_documents',
        'resident.appeals.use': 'can_use_appeals',
        'resident.represent': 'can_represent',
    }.get(capability)
    if not field:
        return False
    grants = _active_period(
        PortalGrant.objects.filter(person_id=person_id, account_id=scope_object_id), on_date
    )
    return grants.filter(**{field: True}).exists()


def _has_service_assignment(person_id, on_date=None):
    on_date = on_date or timezone.localdate()
    for assignment in active_assignments(person_id, on_date):
        if any(not code.startswith('resident.') for code in assignment.capabilities):
            return True
    return False


def sync_service_gateway(person_id, on_date=None):
    """Keep Django is_staff only as the protected /work/ login gateway.

    Business authority remains in V2 assignments. We only undo is_staff when
    this function was the component that enabled it.
    """
    from .resident_models import ResidentIdentity

    identity = ResidentIdentity.objects.select_related('user').filter(person_id=person_id).first()
    if identity is None:
        return None
    user = identity.user
    should_be_staff = _has_service_assignment(person_id, on_date)
    if should_be_staff and not user.is_staff:
        user.is_staff = True
        user.save(update_fields=['is_staff'])
        identity.v2_staff_gateway_managed = True
        identity.save(update_fields=['v2_staff_gateway_managed'])
    elif not should_be_staff and identity.v2_staff_gateway_managed:
        user.is_staff = False
        user.save(update_fields=['is_staff'])
        identity.v2_staff_gateway_managed = False
        identity.save(update_fields=['v2_staff_gateway_managed'])
    return user


@transaction.atomic
def revoke_assignment(assignment, *, actor, reason):
    reason = ' '.join((reason or '').split())
    if not reason:
        raise ValidationError({'reason': 'Укажите причину завершения полномочия.'})
    if assignment.revoked_at:
        raise ValidationError('Полномочие уже завершено.')
    assignment.revoked_at = timezone.now()
    assignment.revoked_by = actor
    assignment.revocation_reason = reason
    assignment._history_user = actor
    assignment._change_reason = f'Полномочие V2 отозвано: {reason}'
    assignment.save(update_fields=['revoked_at', 'revoked_by', 'revocation_reason'])
    sync_service_gateway(assignment.person_id)
    return assignment


@transaction.atomic
def revoke_delegation(delegation, *, actor, reason):
    reason = ' '.join((reason or '').split())
    if not reason:
        raise ValidationError({'reason': 'Укажите причину отзыва делегирования.'})
    if delegation.revoked_at:
        raise ValidationError('Делегирование уже отозвано.')
    delegation.revoked_at = timezone.now()
    delegation.revoked_by = actor
    delegation.revocation_reason = reason
    delegation._history_user = actor
    delegation._change_reason = f'Делегирование V2 отозвано: {reason}'
    delegation.save(update_fields=['revoked_at', 'revoked_by', 'revocation_reason'])
    return delegation


@transaction.atomic
def link_identity(*, user, person, actor, basis):
    """Verified User↔Person link for either resident-only or mixed service use."""
    from .resident_models import ResidentIdentity

    basis = ' '.join((basis or '').split())
    if not basis:
        raise ValidationError({'basis': 'Укажите основание подтверждения личности.'})
    if person.archived:
        raise ValidationError({'person': 'Архивную карточку человека нельзя привязать.'})
    if not user.is_active:
        raise ValidationError({'user': 'Нельзя привязать отключённую учётную запись.'})
    existing_user = ResidentIdentity.objects.filter(user=user).first()
    if existing_user and existing_user.person_id != person.pk:
        raise ValidationError({'user': 'Этот логин уже связан с другим человеком.'})
    existing_person = ResidentIdentity.objects.filter(person=person).first()
    if existing_person and existing_person.user_id != user.pk:
        raise ValidationError({'person': 'Этот человек уже связан с другим логином.'})
    if existing_user:
        return existing_user
    identity = ResidentIdentity(
        user=user, person=person, verified_by=actor, basis=basis,
    )
    identity._history_user = actor
    identity._change_reason = 'Подтверждена единая Person identity для Access V2'
    identity.save()
    sync_service_gateway(person.pk)
    return identity


@transaction.atomic
def transfer_identity(*, identity, new_user, actor, basis):
    """Move a Person to a replacement login without rewriting Person authority history."""
    from .resident_models import ResidentIdentity

    identity = ResidentIdentity.objects.select_for_update().select_related('user').get(pk=identity.pk)
    basis = ' '.join((basis or '').split())
    if not basis:
        raise ValidationError({'basis': 'Укажите основание смены логина.'})
    if not new_user.is_active:
        raise ValidationError({'new_user': 'Новый логин отключён.'})
    if ResidentIdentity.objects.filter(user=new_user).exclude(pk=identity.pk).exists():
        raise ValidationError({'new_user': 'Новый логин уже связан с другим человеком.'})
    old_user = identity.user
    managed = identity.v2_staff_gateway_managed
    identity.user = new_user
    identity.verified_by = actor
    identity.verified_at = timezone.now()
    identity.basis = basis
    identity.v2_staff_gateway_managed = False
    identity._history_user = actor
    identity._change_reason = 'Логин Person заменён без переноса бизнес-прав на новую Person'
    identity.save()
    if managed and old_user.is_staff:
        old_user.is_staff = False
        old_user.save(update_fields=['is_staff'])
    sync_service_gateway(identity.person_id)
    return identity


@transaction.atomic
def update_assignment_capabilities(assignment, *, actor, capabilities, reason):
    """Toggle rights only inside the frozen role envelope recorded at issuance."""
    assignment = AccessAssignment.objects.select_for_update().get(pk=assignment.pk)
    if assignment.revoked_at:
        raise ValidationError('Завершённое назначение нельзя изменять.')
    reason = ' '.join((reason or '').split())
    if not reason:
        raise ValidationError({'reason': 'Укажите основание изменения прав.'})
    selected = _normalize_capabilities(list(capabilities))
    if not selected:
        raise ValidationError({'capabilities': 'Оставьте хотя бы одно право или завершите назначение целиком.'})
    envelope = set(assignment.allowed_capabilities or assignment.capabilities)
    if not set(selected) <= envelope:
        raise ValidationError({'capabilities': 'Нельзя добавить право за пределами исходной роли.'})
    scope = _validate_scope(assignment.scope_type, assignment.scope_object_id)
    _validate_capability_scope(selected, scope)
    assignment.capabilities = selected
    assignment._history_user = actor
    assignment._change_reason = f'Изменён набор прав V2: {reason}'
    assignment.save(update_fields=['capabilities'])
    sync_service_gateway(assignment.person_id)
    return assignment
