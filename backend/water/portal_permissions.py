from dataclasses import dataclass
from datetime import date

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from .models import Account, Person, RecordedModel, ResidentAccess, ResidentAppeal, User
from .resident_models import ResidentIdentity


CAP_VIEW_ACCOUNT = 'view_account'
CAP_FINANCE = 'finance'
CAP_SUBMIT_WATER = 'submit_water'
CAP_DOCUMENTS = 'documents'
CAP_APPEALS = 'appeals'
CAP_REPRESENT = 'represent'


class PortalGrant(RecordedModel):
    """Explicit time-bounded portal authority for one real person and account.

    This is deliberately separate from ownership, membership and the login
    itself. Creating a Person, PlotRelation, TsnMembership or ResidentIdentity
    never creates this grant automatically.
    """

    person = models.ForeignKey(
        Person, verbose_name='Человек', on_delete=models.PROTECT,
        related_name='portal_grants',
    )
    account = models.ForeignKey(
        Account, verbose_name='Лицевой счёт', on_delete=models.PROTECT,
        related_name='portal_grants',
    )
    starts = models.DateField('Доступ с')
    ends = models.DateField('Доступ до (не включая)', blank=True, null=True)
    can_view_account = models.BooleanField('Видеть участок и базовые данные', default=True)
    can_view_finance = models.BooleanField('Видеть начисления и оплаты', default=False)
    can_submit_water = models.BooleanField('Передавать показания воды', default=False)
    can_view_documents = models.BooleanField('Видеть документы лицевого счёта', default=False)
    can_use_appeals = models.BooleanField('Создавать и читать свои обращения', default=False)
    can_represent = models.BooleanField('Совершать представительские действия', default=False)
    basis = models.CharField('Проверенное основание', max_length=300)
    verified_by = models.ForeignKey(
        User, verbose_name='Кто подтвердил', on_delete=models.PROTECT,
        related_name='verified_portal_grants',
    )
    verified_at = models.DateTimeField('Подтверждено', default=timezone.now, editable=False)
    notes = models.TextField('Примечание', blank=True)

    class Meta:
        verbose_name = 'Явное право доступа к кабинету'
        verbose_name_plural = 'Явные права доступа к кабинетам'
        ordering = ['person', 'account', '-starts', 'id']
        constraints = [
            models.CheckConstraint(
                condition=Q(ends__isnull=True) | Q(ends__gt=models.F('starts')),
                name='portal_grant_dates',
            ),
        ]

    def clean(self):
        if self.person_id and self.person.archived:
            raise ValidationError({'person': 'Нельзя выдавать новые права архивной карточке человека.'})
        if self.account_id and self.account.archived:
            raise ValidationError({'account': 'Нельзя выдавать права на архивный лицевой счёт.'})
        if self.verified_by_id and not self.verified_by.is_staff:
            raise ValidationError({'verified_by': 'Подтвердить право может только сотрудник.'})
        if not self.can_view_account and any((
            self.can_view_finance,
            self.can_submit_water,
            self.can_view_documents,
            self.can_use_appeals,
            self.can_represent,
        )):
            raise ValidationError({'can_view_account': 'Специальные права требуют базового доступа к участку.'})
        if not self.person_id or not self.account_id or not self.starts:
            return
        overlaps = PortalGrant.objects.filter(
            person_id=self.person_id,
            account_id=self.account_id,
            starts__lt=self.ends or date.max,
        ).filter(Q(ends__isnull=True) | Q(ends__gt=self.starts)).exclude(pk=self.pk)
        if overlaps.exists():
            raise ValidationError('Для человека уже есть явное право на этот лицевой счёт в пересекающийся период.')

    def __str__(self):
        return f'{self.person} → {self.account}'


@dataclass(frozen=True)
class ResolvedPortalAccess:
    account: Account
    source: str
    role: str
    can_view_account: bool
    can_view_finance: bool
    can_submit_water: bool
    can_view_documents: bool
    can_use_appeals: bool
    can_represent: bool

    @property
    def account_id(self):
        return self.account.pk

    def get_role_display(self):
        if self.source == 'legacy':
            return dict(ResidentAccess._meta.get_field('role').choices).get(self.role, self.role)
        return 'Подтверждённый доступ'

    def allows(self, capability):
        mapping = {
            CAP_VIEW_ACCOUNT: self.can_view_account,
            CAP_FINANCE: self.can_view_finance,
            CAP_SUBMIT_WATER: self.can_submit_water,
            CAP_DOCUMENTS: self.can_view_documents,
            CAP_APPEALS: self.can_use_appeals,
            CAP_REPRESENT: self.can_represent,
        }
        return bool(mapping.get(capability, False))


def _active_period_filter(on_date):
    return Q(ends__isnull=True) | Q(ends__gt=on_date)


def _from_grant(grant):
    return ResolvedPortalAccess(
        account=grant.account,
        source='grant',
        role='verified',
        can_view_account=grant.can_view_account,
        can_view_finance=grant.can_view_finance,
        can_submit_water=grant.can_submit_water,
        can_view_documents=grant.can_view_documents,
        can_use_appeals=grant.can_use_appeals,
        can_represent=grant.can_represent,
    )


def _from_legacy(access):
    # Legacy ResidentAccess historically unlocked the whole account workspace.
    # Preserve that behaviour exactly until each real user is migrated to an
    # explicit PortalGrant. Representative actions are the only future-only
    # capability and therefore stay limited to owner/representative roles.
    return ResolvedPortalAccess(
        account=access.account,
        source='legacy',
        role=access.role,
        can_view_account=True,
        can_view_finance=True,
        can_submit_water=True,
        can_view_documents=True,
        can_use_appeals=True,
        can_represent=access.role in ('owner', 'representative'),
    )


def resolved_accesses_at(user, on_date, capability=None):
    """Resolve portal authority at an exact date without unioning sources."""
    if not getattr(user, 'is_authenticated', False) or not user.is_active or user.is_staff:
        return []

    resolved = {}
    identity = ResidentIdentity.objects.filter(user=user).select_related('person').first()
    if identity:
        grants = PortalGrant.objects.filter(
            person=identity.person,
            account__archived=False,
            starts__lte=on_date,
        ).filter(_active_period_filter(on_date)).select_related('account').order_by('account_id', '-starts', '-id')
        for grant in grants:
            if grant.account_id not in resolved:
                resolved[grant.account_id] = _from_grant(grant)

    legacy = ResidentAccess.objects.filter(
        user=user,
        account__archived=False,
        starts__lte=on_date,
    ).filter(_active_period_filter(on_date)).select_related('account').order_by('account_id', '-starts', '-id')
    for access in legacy:
        if access.account_id not in resolved:
            resolved[access.account_id] = _from_legacy(access)

    rows = sorted(
        resolved.values(),
        key=lambda item: ((item.account.plot or '').casefold(), item.account.number or '', item.account_id),
    )
    if capability:
        rows = [item for item in rows if item.allows(capability)]
    return rows


def resolved_accesses(user, capability=None):
    """Resolve current portal rights, explicit grants first and legacy as fallback."""
    return resolved_accesses_at(user, timezone.localdate(), capability)


def resolved_access_at(user, account_id, capability=CAP_VIEW_ACCOUNT, on_date=None):
    on_date = on_date or timezone.localdate()
    for access in resolved_accesses_at(user, on_date):
        if access.account_id == account_id and access.allows(capability):
            return access
    return None


def resolved_access(user, account_id, capability=CAP_VIEW_ACCOUNT):
    return resolved_access_at(user, account_id, capability, timezone.localdate())


def has_any_portal_access(user):
    return bool(resolved_accesses(user, CAP_VIEW_ACCOUNT))


def _resident_appeal_clean_with_resolver(self):
    """Keep ResidentAppeal's business validation on the shared resolver.

    ResidentAppeal is still declared in the legacy monolithic models.py, while
    PortalGrant lives in this module to avoid enlarging that file. Installing
    this validator from AppConfig.ready() avoids an import cycle back from
    models.py and, importantly, makes admin/board saves obey the same access
    model as resident views.
    """
    if self.author_id and self.author.is_staff:
        raise ValidationError({'author': 'Автором обращения должен быть житель.'})
    if self.author_id and self.account_id:
        opened_on = self.opened_at.date() if self.opened_at else timezone.localdate()
        access = resolved_access_at(self.author, self.account_id, CAP_APPEALS, opened_on)
        if access is None:
            raise ValidationError('У автора нет доступа к этому лицевому счёту на дату обращения.')
    if self.status in ('resolved', 'closed') and not self.response.strip():
        raise ValidationError({'response': 'Для решённого или закрытого обращения укажите ответ.'})


def install_model_permission_validators():
    """Install runtime validators only; no database writes or schema changes."""
    ResidentAppeal.clean = _resident_appeal_clean_with_resolver
