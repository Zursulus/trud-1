"""Public homepage selection and serialization, shared with staff previews."""
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import HomepageAnnouncement, SiteContacts


def active_announcement():
    # Overlapping periods are allowed for planned replacements. Exactly one wins:
    # the latest start date, then the latest created record.
    return HomepageAnnouncement.objects.filter(
        is_published=True, public_checked=True, starts_on__lte=timezone.localdate(),
    ).filter(Q(ends_on__isnull=True) | Q(ends_on__gte=timezone.localdate())).select_related(
        'image', 'document', 'news',
    ).first()


def announcement_payload(item):
    if item is None:
        return None
    url = item.button_url
    today = timezone.localdate()
    if item.document_id:
        target = item.document
        url = reverse('public_document_download', args=[target.pk]) if (
            target.is_published and target.public_checked and target.document_date <= today
        ) else ''
    if item.news_id:
        target = item.news
        url = f'/?news={target.pk}' if (
            target.is_published and target.public_checked and target.published_on <= today
        ) else ''
    return {
        'title': item.title, 'subtitle': item.subtitle, 'body': item.body, 'label': item.label,
        'button_text': item.button_text if url else '', 'button_url': url,
        'image_url': reverse('public_announcement_image', args=[item.pk]) if item.image_id else '',
    }


def contacts_payload():
    item = SiteContacts.objects.filter(is_published=True, public_checked=True).first()
    return {field: getattr(item, field) for field in (
        'email', 'phone', 'address', 'office_hours', 'how_to_contact',
    )} if item else None
