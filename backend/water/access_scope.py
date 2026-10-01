from dataclasses import dataclass
from datetime import date

from django.db.models import Q
from django.utils import timezone

from .access_policy import ScopeType
from .models import Account, Membership, Meter, WaterGroup


@dataclass(frozen=True)
class ScopeRef:
    type: ScopeType
    object_id: int | None = None

    def __post_init__(self):
        if self.type == ScopeType.ALL:
            if self.object_id is not None:
                raise ValueError("all scope cannot have object_id")
        elif self.object_id is None:
            raise ValueError(f"{self.type} scope requires object_id")


def scoped_records(queryset, actor, capability, *, account_field="account_id", person_field=None):
    """Bound private service records to ACCOUNT/PERSON/ALL authority.

    Historical records remain visible within their authorized scope. Other
    scope types fail closed; topology-specific lists use their existing helper.
    """
    from .access_resolver import scopes_for
    if not actor.is_staff:
        return queryset.none()
    predicate = Q(pk__in=[])
    for scope in scopes_for(actor, capability):
        if scope.type == ScopeType.ALL:
            return queryset
        if scope.type == ScopeType.ACCOUNT and account_field:
            predicate |= Q(**{account_field: scope.object_id})
        elif scope.type == ScopeType.PERSON and person_field:
            predicate |= Q(**{person_field: scope.object_id})
    return queryset.filter(predicate)


def can_on_record(actor, capability, *, account_id=None, person_id=None):
    """Check the actual subject; account authority never grants a login reset."""
    from .access_resolver import can
    if not actor.is_staff:
        return False
    scopes = [ScopeRef(ScopeType.ALL)]
    if account_id is not None:
        scopes.append(ScopeRef(ScopeType.ACCOUNT, account_id))
    if person_id is not None:
        scopes.append(ScopeRef(ScopeType.PERSON, person_id))
    return any(can(actor, capability, scope=scope) for scope in scopes)


def on_date_or_today(on_date: date | None) -> date:
    return on_date or timezone.localdate()


def active_memberships(on_date: date | None = None):
    on_date = on_date_or_today(on_date)
    return Membership.objects.filter(starts__lte=on_date).filter(
        Q(ends__isnull=True) | Q(ends__gt=on_date)
    )


def accounts_for_water_group(group_id: int, on_date: date | None = None):
    account_ids = active_memberships(on_date).filter(
        group_id=group_id,
        account__archived=False,
    ).values("account_id")
    return Account.objects.filter(pk__in=account_ids, archived=False)


def accounts_for_supply_node(node_id: int, on_date: date | None = None):
    account_ids = active_memberships(on_date).filter(
        group__node_id=node_id,
        account__archived=False,
    ).values("account_id")
    return Account.objects.filter(pk__in=account_ids, archived=False)


def scope_covers_account(scope: ScopeRef, account_id: int, on_date: date | None = None) -> bool:
    if scope.type == ScopeType.ALL:
        return Account.objects.filter(pk=account_id, archived=False).exists()
    if scope.type == ScopeType.ACCOUNT:
        return scope.object_id == account_id and Account.objects.filter(pk=account_id, archived=False).exists()
    memberships = active_memberships(on_date).filter(account_id=account_id, account__archived=False)
    if scope.type == ScopeType.WATER_GROUP:
        return memberships.filter(group_id=scope.object_id).exists()
    if scope.type == ScopeType.SUPPLY_NODE:
        return memberships.filter(group__node_id=scope.object_id).exists()
    return False


def scope_covers_water_group(scope: ScopeRef, group_id: int) -> bool:
    if scope.type == ScopeType.ALL:
        return WaterGroup.objects.filter(pk=group_id).exists()
    if scope.type == ScopeType.WATER_GROUP:
        return scope.object_id == group_id and WaterGroup.objects.filter(pk=group_id).exists()
    if scope.type == ScopeType.SUPPLY_NODE:
        return WaterGroup.objects.filter(pk=group_id, node_id=scope.object_id).exists()
    return False


def scope_covers_meter(scope: ScopeRef, meter_id: int, on_date: date | None = None) -> bool:
    try:
        meter = Meter.objects.select_related("account", "group").get(pk=meter_id)
    except Meter.DoesNotExist:
        return False

    if scope.type == ScopeType.ALL:
        return True
    if scope.type == ScopeType.SUPPLY_NODE:
        return meter.node_id == scope.object_id
    if scope.type == ScopeType.WATER_GROUP:
        if meter.group_id == scope.object_id:
            return True
        if meter.account_id:
            return scope_covers_account(scope, meter.account_id, on_date)
        return False
    if scope.type == ScopeType.ACCOUNT:
        return meter.account_id == scope.object_id
    return False


def scoped_accounts(scope: ScopeRef, on_date: date | None = None):
    if scope.type == ScopeType.ALL:
        return Account.objects.filter(archived=False)
    if scope.type == ScopeType.ACCOUNT:
        return Account.objects.filter(pk=scope.object_id, archived=False)
    if scope.type == ScopeType.WATER_GROUP:
        return accounts_for_water_group(scope.object_id, on_date)
    if scope.type == ScopeType.SUPPLY_NODE:
        return accounts_for_supply_node(scope.object_id, on_date)
    return Account.objects.none()


def scope_contains(granted: ScopeRef, requested: ScopeRef, on_date: date | None = None) -> bool:
    """Return whether one granted scope contains a requested object scope."""
    if granted.type == ScopeType.ALL:
        return True
    if granted.type == requested.type:
        return granted.object_id == requested.object_id
    if requested.type == ScopeType.ACCOUNT:
        return scope_covers_account(granted, requested.object_id, on_date)
    if requested.type == ScopeType.WATER_GROUP:
        return scope_covers_water_group(granted, requested.object_id)
    return False
