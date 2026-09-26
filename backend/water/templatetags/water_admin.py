from django import template

from water.models import Reading


register = template.Library()


@register.simple_tag
def controller_moderation_context(submission):
    """Return compact comparison data for one controller submission."""
    if not submission or not getattr(submission, 'meter_id', None) or not submission.date:
        return {}

    same_date = (
        Reading.objects.filter(meter_id=submission.meter_id, date=submission.date)
        .order_by('id')
        .first()
    )
    previous = (
        Reading.objects.filter(meter_id=submission.meter_id, date__lt=submission.date)
        .order_by('-date', '-id')
        .first()
    )
    baseline = same_date or previous
    delta = submission.value - baseline.value if baseline is not None else None

    warning = ''
    warning_level = ''
    if delta is not None and delta < 0:
        warning = 'Новое показание меньше предыдущего утверждённого значения. Проверьте ввод перед принятием.'
        warning_level = 'danger'
    elif same_date is not None:
        warning = 'За эту дату уже есть утверждённое показание. Принятие этой заявки будет корректировкой.'
        warning_level = 'warning'

    account = submission.meter.account
    return {
        'address': account.plot if account and account.plot else 'Адрес не заполнен',
        'previous': previous,
        'same_date': same_date,
        'baseline': baseline,
        'delta': delta,
        'has_delta': delta is not None,
        'warning': warning,
        'warning_level': warning_level,
    }
