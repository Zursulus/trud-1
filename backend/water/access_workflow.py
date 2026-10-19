from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
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


class InviteEmailError(Exception):
    """Public mail failure; transport details must not reach the staff page."""


@transaction.atomic
def email_access_request_invite(request_id, *, actor, reason, confirmed, build_absolute_uri):
    locked = ResidentAccessRequest.objects.select_for_update().get(pk=request_id)
    _require_global(actor, 'access.request.decide')
    _require_global(actor, 'access.invite.issue')
    note = _required_note(reason, 'Основание отправки')
    if len(note) > 500:
        raise ValidationError('Основание отправки: не более 500 символов.')
    if confirmed is not True:
        raise ValidationError('Подтвердите проверку адреса и предоставление доступа.')
    if locked.status != ResidentAccessRequest.STATUS_APPROVED or not locked.invite_id:
        raise ValidationError('Отправка доступна только для одобренной заявки с приглашением.')
    previous = ResidentInvite.objects.select_for_update().get(pk=locked.invite_id)
    if previous.used_at:
        raise ValidationError('Приглашение уже использовано жителем. Повторная отправка не требуется.')
    if previous.revoked:
        raise ValidationError('Приглашение отозвано или заменено. Отправка по этой заявке недоступна.')
    if previous.is_granular or (
        previous.account_id != locked.matched_account_id or previous.role != locked.approved_role
    ):
        raise ValidationError('Приглашение не соответствует решению по заявке. Проверьте доступ вручную.')

    reason_text = f'Отправка приглашения по заявке №{locked.pk}. Основание: {note}'
    # Existing simple_history columns are varchar(100). Keep the full reason
    # in the existing admin history of all three objects, without a migration.
    history_reason = reason_text if len(reason_text) <= 100 else reason_text[:97] + '…'
    invite, raw = issue_invite(
        previous.account, previous.email, previous.role, actor=actor,
        change_reason=history_reason,
    )
    locked._replacing_invite_id = previous.pk
    locked.invite = invite
    locked._history_user = actor
    locked._change_reason = reason_text
    locked.save(update_fields=['invite'])
    for obj in (locked, previous, invite):
        LogEntry.objects.create(
            user=actor, content_type=ContentType.objects.get_for_model(obj),
            object_id=str(obj.pk), object_repr=str(obj)[:200], action_flag=CHANGE,
            change_message=f'{locked._change_reason}. Приглашение №{previous.pk} заменено на №{invite.pk}.',
        )

    url = build_absolute_uri(reverse('resident_invite', args=[raw]))
    body = (
        'Здравствуйте!\n\n'
        'ТСН «Труд-1» приглашает вас в личный кабинет жителя. '
        'Сотрудник проверил и одобрил вашу заявку доступа.\n\n'
        f'Для активации доступа перейдите по ссылке:\n{url}\n\n'
        'Ссылка одноразовая и действует 7 дней. Ранее выданная ссылка больше не действует.\n'
        'Если вы не запрашивали доступ — проигнорируйте это письмо.\n'
    )
    try:
        sent = send_mail(
            'Приглашение в личный кабинет ТСН «Труд-1»', body,
            settings.DEFAULT_FROM_EMAIL, [invite.email], fail_silently=False,
        )
        if sent != 1:
            raise InviteEmailError
    except Exception:
        raise InviteEmailError('Письмо не отправлено: почта сайта недоступна') from None
    return locked


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
