from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import PublicDocument, PublicNews
from .homepage import active_announcement, announcement_payload, contacts_payload


@require_GET
def public_content(request):
    today = timezone.localdate()
    news = PublicNews.objects.filter(
        is_published=True,
        public_checked=True,
        published_on__lte=today,
    ).order_by('-is_featured', '-published_on', '-id')
    selected_id = request.GET.get('news')
    if selected_id is not None:
        if (selected_id.isascii() and selected_id.isdecimal() and len(selected_id) <= 19
                and 0 < int(selected_id) <= 9223372036854775807):
            news = news.filter(pk=int(selected_id))
        else:
            news = news.none()
    else:
        news = news[:12]
    article = news.first() if selected_id is not None else None
    documents = PublicDocument.objects.filter(
        is_published=True,
        public_checked=True,
        document_date__lte=today,
    ).select_related('category').order_by('-document_date', '-id')[:60]

    response = JsonResponse({
        'announcement': announcement_payload(active_announcement()),
        'contacts': contacts_payload(),
        'article': news_payload(article) if article else None,
        'forum_url': reverse('forum:index') if settings.FORUM_ENABLED else None,
        'news': [
            news_payload(item)
            for item in news
        ],
        'documents': [
            {
                'id': item.pk,
                'title': item.title,
                'category': item.category.name,
                'description': item.description,
                'date': item.document_date.isoformat(),
                'size': item.file_size,
                'url': reverse('public_document_download', args=[item.pk]),
            }
            for item in documents
        ],
    })
    response['Cache-Control'] = 'public, max-age=60'
    return response


@require_GET
def public_document_download(request, document_id):
    try:
        item = PublicDocument.objects.get(
            pk=document_id,
            is_published=True,
            public_checked=True,
            document_date__lte=timezone.localdate(),
        )
    except PublicDocument.DoesNotExist as exc:
        raise Http404 from exc
    if not item.document:
        raise Http404
    # Public files are deliberately sent as attachments. This prevents an
    # uploaded HTML-like payload from executing in the trud-1.ru origin.
    response = FileResponse(
        item.document.open('rb'),
        as_attachment=True,
        filename=item.original_name or item.document.name.rsplit('/', 1)[-1],
    )
    response['Cache-Control'] = 'public, max-age=300'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


def image_response(image, *, private=False):
    content_type = 'image/png' if image.image.name.lower().endswith('.png') else 'image/jpeg'
    try:
        stream = image.image.open('rb')
    except OSError as exc:
        raise Http404 from exc
    response = FileResponse(stream, content_type=content_type)
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store' if private else 'public, max-age=60'
    return response


@require_GET
def public_announcement_image(request, announcement_id):
    item = active_announcement()
    if item is None or item.pk != announcement_id or not item.image_id:
        raise Http404
    return image_response(item.image)


def news_payload(item):
    return {'id': item.pk, 'title': item.title, 'category': item.category,
            'summary': item.summary, 'body': item.body, 'date': item.published_on.isoformat(),
            'featured': item.is_featured}
