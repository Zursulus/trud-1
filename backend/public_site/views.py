from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import render
from .legal import LegalDocument, current_document
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .models import PublicDocument, PublicNews


@require_GET
def public_content(request):
    today = timezone.localdate()
    news = PublicNews.objects.filter(
        is_published=True,
        public_checked=True,
        published_on__lte=today,
    ).order_by('-is_featured', '-published_on', '-id')[:12]
    documents = PublicDocument.objects.filter(
        is_published=True,
        public_checked=True,
        document_date__lte=today,
    ).select_related('category').order_by('-document_date', '-id')[:60]

    response = JsonResponse({
        'forum_url': reverse('forum:index') if settings.FORUM_ENABLED else None,
        'news': [
            {
                'id': item.pk,
                'title': item.title,
                'category': item.category,
                'summary': item.summary,
                'body': item.body,
                'date': item.published_on.isoformat(),
                'featured': item.is_featured,
            }
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


@require_GET
def legal_document(request, kind):
    if kind not in dict(LegalDocument.KINDS):
        raise Http404
    version = request.GET.get('version')
    document = (LegalDocument.objects.filter(kind=kind, version=version).first()
                if version else current_document(kind))
    if document is None:
        raise Http404
    return render(request, 'public_site/legal_document.html', {'document': document})
