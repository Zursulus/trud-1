from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, F, Q, Sum
from django.utils import timezone


class Command(BaseCommand):
    help = 'Read-only aggregate audit of core business invariants without exposing PII or object identifiers.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--strict', action='store_true',
            help='Exit with an error when a hard invariant violation is found.',
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        model = lambda name: apps.get_model('water', name)
        active = Q(starts__lte=today) & (Q(ends__isnull=True) | Q(ends__gt=today))

        ResidentAccess = model('ResidentAccess')
        PortalGrant = model('PortalGrant')
        PlotRelation = model('PlotRelation')
        Membership = model('Membership')
        BoardMembership = model('BoardMembership')
        ControllerLineAccess = model('ControllerLineAccess')
        Meter = model('Meter')
        Reading = model('Reading')
        PaymentAllocation = model('PaymentAllocation')

        hard = {
            'active_resident_access_archived_account': ResidentAccess.objects.filter(active, account__archived=True).count(),
            'active_portal_grant_archived_account': PortalGrant.objects.filter(active, account__archived=True).count(),
            'active_portal_grant_archived_person': PortalGrant.objects.filter(active, person__archived=True).count(),
            'active_plot_relation_archived_person': PlotRelation.objects.filter(active, person__archived=True).count(),
            'active_plot_relation_archived_plot': PlotRelation.objects.filter(active, plot__archived=True).count(),
            'active_membership_archived_account': Membership.objects.filter(active, account__archived=True).count(),
            'active_board_membership_inactive_user': BoardMembership.objects.filter(active, user__is_active=False).count(),
            'active_controller_access_inactive_user': ControllerLineAccess.objects.filter(active, user__is_active=False).count(),
            'reading_duplicate_meter_date_groups': Reading.objects.values('meter_id', 'date').annotate(n=Count('id')).filter(n__gt=1).count(),
            'reading_future': Reading.objects.filter(date__gt=today).count(),
            'reading_before_commission': Reading.objects.filter(meter__commissioned_on__isnull=False, date__lt=F('meter__commissioned_on')).count(),
            'reading_after_retirement': Reading.objects.filter(meter__retired_on__isnull=False, date__gt=F('meter__retired_on')).count(),
            'finance_cross_account_allocations': PaymentAllocation.objects.exclude(payment__account_id=F('charge__account_id')).count(),
            'meter_individual_without_account': Meter.objects.filter(kind='individual', account__isnull=True).count(),
            'meter_nonindividual_with_account': Meter.objects.exclude(kind='individual').filter(account__isnull=False).count(),
            'meter_line_without_group': Meter.objects.filter(kind='line', group__isnull=True).count(),
            'meter_main_or_irrigation_with_group': Meter.objects.filter(kind__in=('main', 'irrigation'), group__isnull=False).count(),
            'meter_retired_before_commission': Meter.objects.filter(commissioned_on__isnull=False, retired_on__isnull=False, retired_on__lt=F('commissioned_on')).count(),
        }

        payment_over = 0
        for row in PaymentAllocation.objects.values('payment_id', 'payment__amount').annotate(total=Sum('amount')):
            if row['total'] > row['payment__amount']:
                payment_over += 1
        hard['finance_payments_overallocated'] = payment_over

        charge_over = 0
        for row in PaymentAllocation.objects.values('charge_id', 'charge__amount').annotate(total=Sum('amount')):
            if row['total'] > row['charge__amount']:
                charge_over += 1
        hard['finance_charges_overallocated'] = charge_over

        for name, keys in (
            ('ResidentAccess', ('user_id', 'account_id')),
            ('PortalGrant', ('person_id', 'account_id')),
            ('Membership', ('account_id', 'group_id')),
            ('PlotRelation', ('person_id', 'plot_id', 'role')),
            ('BoardMembership', ('user_id',)),
            ('ControllerLineAccess', ('user_id', 'group_id')),
        ):
            groups = model(name).objects.filter(active).values(*keys).annotate(n=Count('id')).filter(n__gt=1).count()
            hard[f'active_duplicate_groups_{name.lower()}'] = groups

        decreases = 0
        previous = {}
        for row in Reading.objects.order_by('meter_id', 'date', 'id').values('meter_id', 'value').iterator():
            old = previous.get(row['meter_id'])
            if old is not None and row['value'] < old:
                decreases += 1
            previous[row['meter_id']] = row['value']
        review = {'reading_decrease_steps': decreases}

        self.stdout.write('=== BUSINESS INTEGRITY AUDIT: READ ONLY / AGGREGATES ONLY ===')
        for key in sorted(hard):
            self.stdout.write(f'hard.{key}={hard[key]}')
        for key in sorted(review):
            self.stdout.write(f'review.{key}={review[key]}')
        self.stdout.write('No PII, filenames or object identifiers were emitted.')

        total_hard = sum(hard.values())
        if options['strict'] and total_hard:
            raise CommandError(f'Hard invariant violations found: {total_hard}. No data was changed.')
