"""Homepage editor. Authorization follows the news editor/publisher boundary."""
from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_GET

from public_site.models import HomepageAnnouncement, SiteContacts, PublicImage, PublicNews
from .staff_documents import PublicationFormBase, _can, _save_public_form
from .staff_workspace import _base_context


class HomepageFormBase(PublicationFormBase):
    change_reason = forms.CharField(
        label='Причина изменения', required=False, max_length=100,
        widget=forms.Textarea(attrs={'rows': 2}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The publisher cannot edit text, but must still submit the version they reviewed.
        self.fields['version'].disabled = False


class AnnouncementForm(HomepageFormBase):
    image_upload = forms.FileField(label='Загрузить новую картинку (PNG или JPEG)', required=False,
                                   help_text='Новая картинка заменит выбор в поле «Картинка». Максимум 5 МБ.')

    class Meta:
        model = HomepageAnnouncement
        fields = ('title', 'subtitle', 'body', 'label', 'button_text', 'button_url', 'document',
                  'news', 'image', 'starts_on', 'ends_on', 'is_published', 'version')
        widgets = {'starts_on': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}),
                   'ends_on': forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}), 'version': forms.HiddenInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.content_editable:
            self.fields['image_upload'].disabled = True

    def clean_image_upload(self):
        upload = self.cleaned_data.get('image_upload')
        if upload:
            image = PublicImage(title='Картинка объявления', image=upload)
            try:
                image.full_clean()
            except ValidationError as error:
                raise forms.ValidationError(error.messages)
        return upload


class ContactsForm(HomepageFormBase):
    class Meta:
        model = SiteContacts
        fields = ('email', 'phone', 'address', 'office_hours', 'how_to_contact', 'is_published', 'version')
        widgets = {'version': forms.HiddenInput()}


@transaction.atomic
def block_edit(request, kind, item_id=None):
    if kind not in {'announcement', 'contacts'}:
        raise Http404
    model, form_class = (HomepageAnnouncement, AnnouncementForm) if kind == 'announcement' else (SiteContacts, ContactsForm)
    capability = f'site.{kind}'
    if not _can(request.user, capability + '.view'):
        raise PermissionDenied
    item = get_object_or_404(model, pk=item_id) if item_id else (model.objects.first() if kind == 'contacts' else None)
    creating = item is None
    can_publish = _can(request.user, capability + '.publish')
    can_edit = _can(request.user, capability + ('.create' if creating else '.edit')) and (
        creating or not item.is_published or can_publish
    )
    can_change = can_edit or (not creating and can_publish)
    if request.method == 'POST' and not can_change:
        raise PermissionDenied
    form = form_class(request.POST if request.method == 'POST' else None,
                      request.FILES if request.method == 'POST' else None,
                      instance=item, can_publish=can_publish, content_editable=can_edit)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                if kind == 'announcement' and form.cleaned_data.get('image_upload'):
                    image = PublicImage(title=form.cleaned_data['title'], image=form.cleaned_data['image_upload'])
                    image._history_user = request.user
                    image._change_reason = 'Загрузка картинки объявления'
                    image.save()
                    form.instance.image = image
                item = _save_public_form(form, actor=request.user, can_publish=can_publish)
        except ValidationError as error:
            form.add_error(None, '; '.join(error.messages))
        else:
            messages.success(request, 'Блок сайта сохранён.')
            return HttpResponseRedirect(reverse('staff_workspace:site_block', args=[kind, item.pk]))
    context = _base_context(request, section='documents')
    context.update(form=form, item=item, can_change=can_change, can_publish=can_publish,
                   kind=kind, block_title=model._meta.verbose_name)
    return TemplateResponse(request, 'water/work/documents/block_form.html', context)


@require_GET
def preview(request, kind, item_id):
    if kind not in {'news', 'announcement', 'contacts'}:
        raise Http404
    if kind == 'news':
        model, capability = PublicNews, 'news.view'
    elif kind == 'announcement':
        model, capability = HomepageAnnouncement, 'site.announcement.view'
    else:
        model, capability = SiteContacts, 'site.contacts.view'
    if not _can(request.user, capability):
        raise PermissionDenied
    item = get_object_or_404(model, pk=item_id)
    context = _base_context(request, section='documents')
    from public_site.homepage import announcement_payload
    payload = {'news': [], 'documents': [], 'announcement': None, 'contacts': None}
    if kind == 'news':
        from public_site.views import news_payload
        payload['news'] = [news_payload(item)]
    elif kind == 'announcement':
        payload['announcement'] = announcement_payload(item)
        if item.image_id:
            payload['announcement']['image_url'] = reverse('staff_workspace:site_preview_image', args=[item.pk])
    else:
        payload['contacts'] = {name: getattr(item, name) for name in (
            'email', 'phone', 'address', 'office_hours', 'how_to_contact',
        )}
    context.update(item=item, kind=kind, preview_payload=payload)
    response = TemplateResponse(request, 'water/work/documents/preview.html', context)
    response['Cache-Control'] = 'private, no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


@require_GET
def preview_image(request, item_id):
    if not _can(request.user, 'site.announcement.view'):
        raise PermissionDenied
    item = get_object_or_404(HomepageAnnouncement.objects.select_related('image'), pk=item_id)
    if not item.image_id:
        raise Http404
    from public_site.views import image_response
    return image_response(item.image, private=True)


workspace_block = admin.site.admin_view(block_edit)
workspace_preview = admin.site.admin_view(preview)
workspace_preview_image = admin.site.admin_view(preview_image)
