from django.db.models.signals import pre_save
from django.dispatch import receiver

from .access_policy import ScopeType
from .access_resolver import can
from .access_scope import ScopeRef
from .models import ControllerReadingSubmission, Meter, User


SELF_REVIEW_NOTE = (
    'Самопроверка исключена: автор показания имеет полномочие проверки этой линии; '
    'передано администратору напрямую.'
)


def _route_around_self_review(submission):
    submission.line_review_status = ControllerReadingSubmission.LINE_REVIEW_NOT_REQUIRED
    submission.line_reviewed_by = None
    submission.line_reviewed_at = None
    submission.line_review_comment = SELF_REVIEW_NOTE
    submission._change_reason = 'Самопроверка старшего линии исключена; передано администратору'


@receiver(
    pre_save,
    sender=ControllerReadingSubmission,
    dispatch_uid='water.prevent_line_senior_self_review',
)
def prevent_line_senior_self_review(sender, instance, **kwargs):
    if instance.source != ControllerReadingSubmission.SOURCE_RESIDENT or instance.status != 'pending':
        return
    if not instance.submitted_by_id or not instance.meter_id or not instance.date:
        return

    # Fail closed for every write path, including crafted/admin posts: the author
    # can never become their own line reviewer. Route to final admin review instead.
    if instance.line_reviewed_by_id == instance.submitted_by_id:
        _route_around_self_review(instance)
        return

    if instance.line_review_status != ControllerReadingSubmission.LINE_REVIEW_PENDING:
        return

    account_id = Meter.objects.filter(pk=instance.meter_id).values_list('account_id', flat=True).first()
    if not account_id:
        return
    submitter = User.objects.filter(pk=instance.submitted_by_id, is_active=True).first()
    if submitter is None:
        return

    if can(
        submitter,
        'water.observation.review_line',
        scope=ScopeRef(ScopeType.ACCOUNT, account_id),
        on_date=instance.date,
    ):
        _route_around_self_review(instance)
