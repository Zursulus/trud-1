from collections import defaultdict
from dataclasses import dataclass, field

from django.db.models import Q
from django.utils import timezone

from .models import PlotRelation, ResidentAccess
from .portal_permissions import PortalGrant
from .private_registry import MemberRegistryEntry
from .resident_models import ResidentIdentity
from .resident_numbers import (
    RESIDENT_NUMBER_FIRST,
    RESIDENT_NUMBER_LAST,
    ResidentNumberSlot,
)


CATEGORY_ORDER = (
    'eligible',
    'excluded_nonresident',
    'shared_contact',
    'person_linked',
    'person_manual_required',
    'account_linked',
    'account_missing',
    'user_assigned',
    'user_missing',
    'identity_existing',
    'identity_candidate',
    'identity_conflict',
    'unique_plot',
    'plot_missing',
    'plot_ambiguous',
    'plot_relation_existing',
    'plot_relation_candidate',
    'portal_grant_existing',
    'portal_grant_candidate',
    'legacy_access_present',
    'manual_review',
)

CATEGORY_LABELS = {
    'eligible': 'Допустимые записи закрытого реестра',
    'excluded_nonresident': 'Исключены как не реальные resident №1–310',
    'shared_contact': 'Повторяющийся телефон/email — только ручная проверка',
    'person_linked': 'Уже связан Person',
    'person_manual_required': 'Нужна ручная идентификация Person',
    'account_linked': 'Уже связан Account',
    'account_missing': 'Нет связанного Account',
    'user_assigned': 'Resident № уже связан с User',
    'user_missing': 'Resident № пока без User',
    'identity_existing': 'ResidentIdentity уже существует и совпадает',
    'identity_candidate': 'Можно рассмотреть связь User ↔ Person после ручной проверки',
    'identity_conflict': 'Есть конфликт существующей ResidentIdentity',
    'unique_plot': 'У Account один действующий LandPlot',
    'plot_missing': 'У Account нет действующего LandPlot',
    'plot_ambiguous': 'У Account несколько действующих LandPlot',
    'plot_relation_existing': 'Есть действующая PlotRelation Person ↔ LandPlot',
    'plot_relation_candidate': 'Можно рассмотреть PlotRelation, роль только вручную',
    'portal_grant_existing': 'Есть действующий PortalGrant Person → Account',
    'portal_grant_candidate': 'Можно рассмотреть PortalGrant, capabilities только вручную',
    'legacy_access_present': 'Есть действующий legacy ResidentAccess',
    'manual_review': 'Итог: требуется ручная проверка до любых записей',
}


@dataclass
class RegistryMigrationPlan:
    """PII-free, read-only classification of the closed member registry."""

    categories: dict[str, set[int]] = field(
        default_factory=lambda: {name: set() for name in CATEGORY_ORDER}
    )

    def add(self, category, resident_number):
        self.categories[category].add(int(resident_number))

    def count(self, category):
        return len(self.categories[category])

    def ids(self, category):
        return sorted(self.categories[category])

    @property
    def total_seen(self):
        return self.count('eligible') + self.count('excluded_nonresident')

    def lines(self, *, show_ids=False):
        lines = [
            f'Закрытый реестр: просмотрено {self.total_seen}; допустимых {self.count("eligible")}; '
            f'исключено {self.count("excluded_nonresident")}.',
            'DRY-RUN ONLY: база данных не изменяется; Person, членство, PlotRelation и PortalGrant не создаются.',
            'Телефоны, email, ФИО и другие ПД намеренно не выводятся.',
            'TsnMembership: 0 кандидатов — членство нельзя выводить из участка, контакта или записи реестра.',
        ]
        for category in CATEGORY_ORDER:
            label = CATEGORY_LABELS[category]
            line = f'{label}: {self.count(category)}'
            if show_ids and self.categories[category]:
                line += ' · № ' + ', '.join(map(str, self.ids(category)))
            lines.append(line)
        return lines


def _norm_phone(value):
    digits = ''.join(char for char in (value or '') if char.isdigit())
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    return digits


def _norm_email(value):
    return (value or '').strip().casefold()


def _active_period(on_date):
    return Q(ends__isnull=True) | Q(ends__gt=on_date)


def analyze_member_registry(*, on_date=None):
    """Inspect the DB and return only resident-number classifications.

    Contacts are used solely to detect collisions. They are never returned and
    never treated as identity evidence. The function performs SELECTs only.
    """

    on_date = on_date or timezone.localdate()
    plan = RegistryMigrationPlan()
    entries = list(
        MemberRegistryEntry.objects.select_related(
            'resident_number', 'resident_number__user', 'person', 'account'
        ).order_by('resident_number_id')
    )

    phone_groups = defaultdict(set)
    email_groups = defaultdict(set)
    for entry in entries:
        number = entry.resident_number_id
        slot = entry.resident_number
        if not (
            RESIDENT_NUMBER_FIRST <= number <= RESIDENT_NUMBER_LAST
            and slot.purpose == ResidentNumberSlot.PURPOSE_RESIDENT
        ):
            continue
        phone = _norm_phone(entry.phone)
        email = _norm_email(entry.email)
        if phone:
            phone_groups[phone].add(number)
        if email:
            email_groups[email].add(number)

    shared_numbers = set()
    for group in (*phone_groups.values(), *email_groups.values()):
        if len(group) > 1:
            shared_numbers.update(group)

    for entry in entries:
        number = entry.resident_number_id
        slot = entry.resident_number
        if not (
            RESIDENT_NUMBER_FIRST <= number <= RESIDENT_NUMBER_LAST
            and slot.purpose == ResidentNumberSlot.PURPOSE_RESIDENT
        ):
            plan.add('excluded_nonresident', number)
            continue

        plan.add('eligible', number)
        manual = False
        if number in shared_numbers:
            plan.add('shared_contact', number)
            manual = True

        person = entry.person if entry.person_id and not entry.person.archived else None
        if person:
            plan.add('person_linked', number)
        else:
            plan.add('person_manual_required', number)
            manual = True

        account = entry.account if entry.account_id and not entry.account.archived else None
        if account:
            plan.add('account_linked', number)
        else:
            plan.add('account_missing', number)
            manual = True

        user = slot.user if slot.user_id and not slot.user.is_staff and slot.user.is_active else None
        if user:
            plan.add('user_assigned', number)
        else:
            plan.add('user_missing', number)

        if user and person:
            by_user = ResidentIdentity.objects.filter(user=user).first()
            by_person = ResidentIdentity.objects.filter(person=person).first()
            if by_user and by_person and by_user.pk == by_person.pk:
                plan.add('identity_existing', number)
            elif by_user or by_person:
                plan.add('identity_conflict', number)
                manual = True
            elif number not in shared_numbers:
                plan.add('identity_candidate', number)

        unique_plot = None
        if account:
            plots = list(account.land_plots.filter(archived=False).order_by('id')[:2])
            if not plots:
                plan.add('plot_missing', number)
                manual = True
            elif len(plots) == 1:
                unique_plot = plots[0]
                plan.add('unique_plot', number)
            else:
                plan.add('plot_ambiguous', number)
                manual = True

        if person and unique_plot:
            relation = PlotRelation.objects.filter(
                person=person,
                plot=unique_plot,
                starts__lte=on_date,
            ).filter(_active_period(on_date)).first()
            if relation:
                plan.add('plot_relation_existing', number)
            elif number not in shared_numbers:
                plan.add('plot_relation_candidate', number)

        if person and account:
            grant = PortalGrant.objects.filter(
                person=person,
                account=account,
                starts__lte=on_date,
            ).filter(_active_period(on_date)).first()
            if grant:
                plan.add('portal_grant_existing', number)
            elif number not in shared_numbers:
                plan.add('portal_grant_candidate', number)

        if user and account:
            legacy = ResidentAccess.objects.filter(
                user=user,
                account=account,
                starts__lte=on_date,
            ).filter(_active_period(on_date)).exists()
            if legacy:
                plan.add('legacy_access_present', number)

        if manual:
            plan.add('manual_review', number)

    return plan
