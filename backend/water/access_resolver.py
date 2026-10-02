from dataclasses import dataclass
from datetime import date

from django.utils import timezone

from .access_policy import CAPABILITIES, ScopeType
from .access_scope import ScopeRef, scope_contains, scope_covers_account
from .board_polls import active_board_membership
from .access_control import (
    active_assignments, active_delegations, direct_person_has_authority_on,
)
from .controller_scope import ControllerLineAccess
from .portal_permissions import (
    CAP_APPEALS,
    CAP_DOCUMENTS,
    CAP_FINANCE,
    CAP_REPRESENT,
    CAP_SUBMIT_WATER,
    CAP_VIEW_ACCOUNT,
    resolved_access_at,
)
from .resident_models import ResidentIdentity


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    capability: str
    scope: ScopeRef | None
    source: str
    reason: str
    person_id: int | None = None
    source_id: int | None = None


PORTAL_CAPABILITIES = {
    "resident.account.view": CAP_VIEW_ACCOUNT,
    "resident.finance.view": CAP_FINANCE,
    "resident.water.submit": CAP_SUBMIT_WATER,
    "resident.documents.view": CAP_DOCUMENTS,
    "resident.appeals.use": CAP_APPEALS,
    "resident.represent": CAP_REPRESENT,
}


LINE_SENIOR_CAPABILITIES = {
    "accounts.view",
    "water.view",
    "water.meters.view",
    "water.reading.view",
    "water.topology.view",
    "water.line_submission.submit",
    "water.observation.review_line",
    "water.balance.view",
}


# Exact current global checks where a semantic capability has a safe compatibility
# mapping. Missing entries intentionally deny until that domain is migrated rather
# than guessing a broader permission.
LEGACY_PERMISSION_REQUIREMENTS = {
    "accounts.view": ("water.view_account",),
    "accounts.edit": ("water.change_account",),
    "accounts.export": ("water.export_account",),
    "plots.view": ("water.view_landplot",),
    "plots.edit": ("water.change_landplot",),
    "relations.view": ("water.view_historicalplotrelation",),
    "water.view": ("water.view_meter",),
    "water.meters.view": ("water.view_meter",),
    "water.reading.view": ("water.view_reading",),
    "water.topology.view": ("water.view_watergroup",),
    "water.reading.submit_official": ("water.add_reading",),
    "water.observation.submit": ("water.add_controllerreadingsubmission",),
    "water.observation.finalize": ("water.change_controllerreadingsubmission",),
    "water.balance.view": ("water.view_reading", "water.view_meter", "water.view_watergroup"),
    "water.export": ("water.export_reading",),
    "finance.view": (
        "water.view_account", "water.view_billingperiod", "water.view_charge",
        "water.view_payment", "water.view_paymentallocation",
    ),
    "finance.period.calculate": ("water.change_billingperiod", "water.add_charge", "water.change_charge"),
    "finance.charge.approve": ("water.change_charge",),
    "finance.charge.cancel": ("water.change_charge",),
    "finance.period.approve": ("water.change_billingperiod",),
    "finance.period.close": ("water.change_billingperiod",),
    "finance.payment.create": ("water.add_payment",),
    "finance.payment.confirm": ("water.change_payment",),
    "finance.payment.reverse": ("water.change_payment",),
    "finance.payment.allocate": ("water.add_paymentallocation",),
    "finance.policy.manage": ("water.change_billingpolicy",),
    "finance.export": ("water.view_payment", "water.view_charge"),
    "appeals.view": ("water.view_residentappeal",),
    "appeals.reply": ("water.change_residentappeal",),
    "appeals.status.change": ("water.change_residentappeal",),
    "appeals.close": ("water.change_residentappeal",),
    "appeals.attachment.view": ("water.view_residentappeal",),
    "appeals.attachment.manage": ("water.change_residentappeal",),
    "documents.account.view": ("water.view_accountdocument",),
    "documents.account.create": ("water.add_accountdocument",),
    "documents.account.edit_metadata": ("water.change_accountdocument",),
    "documents.account.download": ("water.view_accountdocument",),
    "documents.public.view": ("public_site.view_publicdocument",),
    "documents.public.create": ("public_site.add_publicdocument",),
    "documents.public.edit": ("public_site.change_publicdocument",),
    "documents.public.publish": ("public_site.change_publicdocument",),
    "news.view": ("public_site.view_publicnews",),
    "news.create": ("public_site.add_publicnews",),
    "news.edit": ("public_site.change_publicnews",),
    "news.publish": ("public_site.change_publicnews",),
    "access.request.review": ("water.access_private_registry", "water.view_residentaccessrequest"),
    "access.request.decide": ("water.access_private_registry", "water.change_residentaccessrequest"),
    "access.identity.verify": ("water.access_private_registry",),
    "access.person.create": ("water.add_person",),
    "access.invite.issue": ("water.access_private_registry", "water.add_residentinvite"),
    "access.invite.revoke": ("water.change_residentinvite",),
    "access.grant.view": ("water.view_portalgrant",),
    "access.grant.edit": ("water.change_portalgrant",),
    "access.grant.end": ("water.change_portalgrant",),
    "access.password_reset.issue": ("water.add_residentpasswordreset",),
    "access.password_reset.revoke": ("water.change_residentpasswordreset",),
    "registry.view": ("water.access_private_registry",),
    "registry.contacts.view": ("water.access_private_registry",),
    "registry.edit": ("water.access_private_registry", "water.change_person"),
    "governance.board.view": ("water.view_boardpoll",),
    "governance.poll.create": ("water.add_boardpoll", "water.add_boardquestion"),
    "governance.poll.edit": ("water.change_boardpoll",),
    "governance.poll.close": ("water.change_boardpoll",),
    "governance.protocol.add": ("water.add_boardprotocol",),
    "governance.audit.view": ("water.view_boardauditevent",),
    "governance.membership.manage": ("water.change_boardmembership",),
    "system.import.stage": ("water.add_importbatch",),
    "system.import.apply": ("water.change_importbatch",),
}


def _identity_person_id(user):
    if hasattr(user, "_access_v2_person_id_cache"):
        return user._access_v2_person_id_cache
    identity = ResidentIdentity.objects.filter(user=user).only("person_id").first()
    user._access_v2_person_id_cache = identity.person_id if identity else None
    return user._access_v2_person_id_cache


def _active_line_accesses(user, on_date):
    from django.db.models import Q

    return ControllerLineAccess.objects.filter(
        user=user,
        starts__lte=on_date,
    ).filter(Q(ends__isnull=True) | Q(ends__gt=on_date))


def _line_decision(user, capability, scope, on_date):
    if capability not in LINE_SENIOR_CAPABILITIES:
        return None
    if not user.has_perm("water.use_controller_workspace"):
        return None

    accesses = _active_line_accesses(user, on_date)
    if scope is None:
        return None

    matched = None
    if scope.type == ScopeType.WATER_GROUP:
        matched = accesses.filter(group_id=scope.object_id).first()
    elif scope.type == ScopeType.ACCOUNT:
        for access in accesses.select_related("group"):
            if scope_covers_account(
                ScopeRef(ScopeType.WATER_GROUP, access.group_id),
                scope.object_id,
                on_date,
            ):
                matched = access
                break

    if matched is None:
        return None
    return AccessDecision(
        True,
        capability,
        scope,
        "legacy_controller_line_access",
        "Разрешено действующим назначением на линию; до миграции эта запись исторически смешивает старшего и контролёра.",
        person_id=_identity_person_id(user),
        source_id=matched.pk,
    )


def _assignment_scope(assignment):
    scope_type = ScopeType(assignment.scope_type)
    if scope_type == ScopeType.ALL:
        return ScopeRef(scope_type)
    if scope_type == ScopeType.SELF:
        return ScopeRef(scope_type, assignment.person_id)
    return ScopeRef(scope_type, assignment.scope_object_id)


def _v2_assignment_decision(person_id, capability, scope, on_date):
    if not person_id:
        return None
    for assignment in active_assignments(person_id, on_date):
        if capability not in assignment.capabilities:
            continue
        granted_scope = _assignment_scope(assignment)
        if scope is None:
            if granted_scope.type != ScopeType.ALL:
                continue
        elif not scope_contains(granted_scope, scope, on_date):
            continue
        return AccessDecision(
            True, capability, scope, 'v2_assignment',
            f'Разрешено назначением «{assignment.role_label}» V2.',
            person_id=person_id, source_id=assignment.pk,
        )
    return None


def _v2_delegation_decision(person_id, capability, scope, on_date):
    if not person_id or scope is None or scope.type != ScopeType.ACCOUNT:
        return None
    for delegation in active_delegations(person_id, on_date).filter(
        scope_type=ScopeType.ACCOUNT.value, scope_object_id=scope.object_id,
    ):
        if capability not in delegation.capabilities:
            continue
        if not direct_person_has_authority_on(
            delegation.delegator_id, capability, ScopeType.ACCOUNT, scope.object_id, on_date,
        ):
            continue
        return AccessDecision(
            True, capability, scope, 'v2_delegation',
            'Разрешено действующим делегированием прямого личного полномочия.',
            person_id=person_id, source_id=delegation.pk,
        )
    return None


def _portal_decision(user, capability, scope, on_date):
    portal_cap = PORTAL_CAPABILITIES.get(capability)
    if portal_cap is None or scope is None or scope.type != ScopeType.ACCOUNT:
        return None
    access = resolved_access_at(user, scope.object_id, portal_cap, on_date)
    if access is None:
        return None
    return AccessDecision(
        True,
        capability,
        scope,
        f"portal_{access.source}",
        "Разрешено действующим доступом личного кабинета.",
        person_id=_identity_person_id(user),
        source_id=None,
    )


def resolve(user, capability: str, *, scope: ScopeRef | None = None, on_date: date | None = None) -> AccessDecision:
    if capability not in CAPABILITIES:
        raise ValueError(f"Unknown capability: {capability}")
    on_date = on_date or timezone.localdate()

    if not getattr(user, "is_authenticated", False):
        return AccessDecision(False, capability, scope, "authentication", "Требуется вход.")
    if not user.is_active:
        return AccessDecision(False, capability, scope, "inactive_user", "Учётная запись отключена.")

    person_id = _identity_person_id(user)

    if user.is_superuser:
        return AccessDecision(
            True, capability, scope, "superuser", "Технический superuser; действие должно аудитироваться как break-glass.",
            person_id=person_id,
        )

    assignment = _v2_assignment_decision(person_id, capability, scope, on_date)
    if assignment is not None:
        return assignment

    delegation = _v2_delegation_decision(person_id, capability, scope, on_date)
    if delegation is not None:
        return delegation

    portal = _portal_decision(user, capability, scope, on_date)
    if portal is not None:
        return portal

    line = _line_decision(user, capability, scope, on_date)
    if line is not None:
        return line

    if capability == "governance.board.view" and active_board_membership(user, on_date) is not None:
        membership = active_board_membership(user, on_date)
        return AccessDecision(
            True, capability, scope, "board_membership", "Разрешено действующим членством в правлении.",
            person_id=person_id,
            source_id=membership.pk,
        )

    required = LEGACY_PERMISSION_REQUIREMENTS.get(capability)
    if required and getattr(user, "is_staff", False) and all(user.has_perm(code) for code in required):
        return AccessDecision(
            True,
            capability,
            scope,
            "django_permission_compat",
            "Разрешено текущими Django permissions; будет заменено V2 assignment при миграции модуля.",
            person_id=person_id,
        )

    return AccessDecision(
        False,
        capability,
        scope,
        "deny_by_default",
        "Нет действующего источника полномочия для этой capability и области.",
        person_id=person_id,
    )


def can(user, capability: str, *, scope: ScopeRef | None = None, on_date: date | None = None) -> bool:
    return resolve(user, capability, scope=scope, on_date=on_date).allowed


def explain(user, capability: str, *, scope: ScopeRef | None = None, on_date: date | None = None) -> AccessDecision:
    return resolve(user, capability, scope=scope, on_date=on_date)


def scopes_for(user, capability: str, *, on_date: date | None = None) -> list[ScopeRef]:
    """Return every current scope that can authorize a capability.

    Used by list/workspace views that must build a bounded queryset before an
    individual object exists. Object actions must still call resolve()/can().
    """
    if capability not in CAPABILITIES:
        raise ValueError(f"Unknown capability: {capability}")
    on_date = on_date or timezone.localdate()
    if not getattr(user, 'is_authenticated', False) or not user.is_active:
        return []
    if user.is_superuser:
        return [ScopeRef(ScopeType.ALL)]

    result = []
    person_id = _identity_person_id(user)
    if person_id:
        cache = getattr(user, "_access_v2_assignments_cache", None)
        if cache is None:
            cache = {}
            user._access_v2_assignments_cache = cache
        assignments = cache.get(on_date)
        if assignments is None:
            assignments = list(active_assignments(person_id, on_date))
            cache[on_date] = assignments
        for assignment in assignments:
            if capability in assignment.capabilities:
                result.append(_assignment_scope(assignment))

    if capability in LINE_SENIOR_CAPABILITIES and user.has_perm('water.use_controller_workspace'):
        result.extend(
            ScopeRef(ScopeType.WATER_GROUP, access.group_id)
            for access in _active_line_accesses(user, on_date)
        )

    if capability == 'governance.board.view' and active_board_membership(user, on_date) is not None:
        result.append(ScopeRef(ScopeType.SELF, person_id or user.pk))

    required = LEGACY_PERMISSION_REQUIREMENTS.get(capability)
    if required and getattr(user, 'is_staff', False) and all(user.has_perm(code) for code in required):
        result.append(ScopeRef(ScopeType.ALL))

    # Deduplicate while preserving the most readable/stable order.
    seen = set()
    unique = []
    for item in result:
        key = (item.type.value, item.object_id)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def can_any(user, capability: str, *, on_date: date | None = None) -> bool:
    """Whether the user has this capability in at least one current scope."""
    return bool(scopes_for(user, capability, on_date=on_date))
