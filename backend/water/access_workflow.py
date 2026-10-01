from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .access_requests import ResidentAccessRequest
from .models import ResidentAccess, ResidentInvite, ResidentPasswordReset, User
from .portal import issue_invite, issue_password_reset
from .portal_permissions import PortalGrant
from .resident_models import ResidentIdentity
from .access_resolver import can
from .access_scope import ScopeRef
from .access_policy import ScopeType


def _require_global(actor, capability, *, legacy_permission=None):
    if not actor or not actor.is_staff or not (
        can(actor, capability, scope=ScopeRef(ScopeType.ALL))
        or (legacy_permission and actor.has_perm(legacy_permission))
    ):
        raise PermissionDenied


def _required_note(value, label='Основание решения'):
    note = (value or '').strip()
    if not note:
        raise ValidationError(f'{label}: обязательно заполнить.')
    return note


@transaction.atomic
def approve_access_request(request_id, *, account, email, role, decision_note, actor):
    """Approve one immutable request and issue exactly one one-time invite."""
    locked = ResidentAccessRequest.objects.select_for_update().get(pk=request_id)
    _require_global(actor, 'access.request.decide')
    _require_global(actor, 'access.invite.issue')
    if locked.status != ResidentAccessRequest.STATUS_NEW:
        raise ValidationError('По этой заявке решение уже принято.')
    note = _required_note(decision_note)
    email = (email or '').strip().lower()
    active_matches = User.objects.filter(email__iexact=email, is_active=True).count()
    if not locked.requester_user_id and active_matches > 1:
        raise ValidationError(
            'Этот email связан с несколькими активными учётными записями. '
            'Сначала устраните неоднозначность или укажите email конкретного кабинета.'
        )
    invite, raw = issue_invite(account, email, role, actor=actor)
    locked.status = ResidentAccessRequest.STATUS_APPROVED
    locked.matched_account = account
    locked.approved_role = role
    locked.decision_note = note
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.invite = invite
    locked.save()
    return locked, invite, raw


@transaction.atomic
def reject_access_request(request_id, *, decision_note, actor):
    """Reject one immutable access request without creating portal authority."""
    locked = ResidentAccessRequest.objects.select_for_update().get(pk=request_id)
    _require_global(actor, 'access.request.decide')
    if locked.status != ResidentAccessRequest.STATUS_NEW:
        raise ValidationError('По этой заявке решение уже принято.')
    locked.status = ResidentAccessRequest.STATUS_REJECTED
    locked.decision_note = _required_note(decision_note, 'Причина отклонения')
    locked.decided_by = actor
    locked.decided_at = timezone.now()
    locked.save()
    return locked


@transaction.atomic
def revoke_invite(invite_id, *, actor):
    invite = ResidentInvite.objects.select_for_update().get(pk=invite_id)
    _require_global(actor, 'access.invite.revoke')
    if invite.used_at:
        raise ValidationError('Использованное приглашение нельзя отозвать.')
    if invite.revoked:
        raise ValidationError('Приглашение уже отозвано.')
    invite.revoked = True
    invite._history_user = actor
    invite._change_reason = 'Приглашение отозвано сотрудником'
    invite.save(update_fields=['revoked'])
    return invite


@transaction.atomic
def revoke_password_reset(reset_id, *, actor):
    reset = ResidentPasswordReset.objects.select_for_update().get(pk=reset_id)
    _require_global(actor, 'access.password_reset.revoke')
    if reset.used_at:
        raise ValidationError('Использованную ссылку восстановления нельзя отозвать.')
    if reset.revoked:
        raise ValidationError('Ссылка восстановления уже отозвана.')
    reset.revoked = True
    reset._history_user = actor
    reset._change_reason = 'Ссылка восстановления отозвана сотрудником'
    reset.save(update_fields=['revoked'])
    return reset


@transaction.atomic
def end_resident_access(access_id, *, ends_on, actor):
    """End an active access interval; never delete or rewrite its beginning."""
    access = ResidentAccess.objects.select_for_update().get(pk=access_id)
    _require_global(actor, 'access.grant.end', legacy_permission='water.change_residentaccess')
    if access.ends is not None:
        raise ValidationError('Доступ уже завершён.')
    access.ends = ends_on
    access._history_user = actor
    access._change_reason = 'Доступ жителя завершён сотрудником'
    access.full_clean()
    access.save(update_fields=['ends'])
    return access


@transaction.atomic
def issue_access_password_reset(access_id, *, actor):
    access = ResidentAccess.objects.select_for_update().select_related('user').get(pk=access_id)
    _require_global(actor, 'access.password_reset.issue')
    return issue_password_reset(access.user, actor=actor)


@transaction.atomic
def end_portal_grant(grant_id, *, ends_on, actor):
    grant = PortalGrant.objects.select_for_update().get(pk=grant_id)
    _require_global(actor, 'access.grant.end')
    if grant.ends is not None:
        raise ValidationError('Доступ уже завершён.')
    if ends_on <= grant.starts:
        raise ValidationError('Дата завершения должна быть позже даты начала доступа.')
    grant.ends = ends_on
    grant._history_user = actor
    grant._change_reason = 'Явный доступ жителя завершён сотрудником'
    grant.save(update_fields=['ends'])
    return grant


@transaction.atomic
def issue_grant_password_reset(grant_id, *, actor):
    grant = PortalGrant.objects.select_for_update().get(pk=grant_id)
    _require_global(actor, 'access.password_reset.issue')
    identity = ResidentIdentity.objects.select_related('user').filter(person=grant.person).first()
    if identity is None:
        raise ValidationError('К этому доступу ещё не привязан активированный кабинет жителя.')
    return issue_password_reset(identity.user, actor=actor)


@transaction.atomic
def update_portal_grant_rights(grant_id, *, actor, can_view_account, can_view_finance,
                               can_submit_water, can_view_documents, can_use_appeals,
                               can_represent):
    """Change only the capability snapshot of an active explicit resident grant."""
    grant = PortalGrant.objects.select_for_update().get(pk=grant_id)
    _require_global(actor, 'access.grant.edit')
    today = timezone.localdate()
    if grant.ends is not None and grant.ends <= today:
        raise ValidationError('Завершённый доступ нельзя расширять или изменять.')
    fields = {
        'can_view_account': bool(can_view_account),
        'can_view_finance': bool(can_view_finance),
        'can_submit_water': bool(can_submit_water),
        'can_view_documents': bool(can_view_documents),
        'can_use_appeals': bool(can_use_appeals),
        'can_represent': bool(can_represent),
    }
    for name, value in fields.items():
        setattr(grant, name, value)
    grant._history_user = actor
    grant._change_reason = 'Изменён явный набор личных прав жителя'
    grant.full_clean()
    grant.save(update_fields=list(fields))
    return grant
