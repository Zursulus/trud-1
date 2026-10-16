"""Separate consent decisions; neither forum rules nor privacy notice imply consent."""
from django import forms
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.html import format_html
from django.urls import reverse

from config.network import client_ip
from public_site.legal import current_document
from .models import ConsentRecord


def accepted_current(user, kind):
    document = current_document('consent' if kind == 'binding' else kind)
    latest = ConsentRecord.objects.filter(user=user, document_type=kind).order_by('-pk').first()
    return bool(document and latest and latest.action == 'accept' and latest.document_id == document.pk)


class ConsentForm(forms.Form):
    consent = forms.BooleanField(label='Даю согласие на обработку ПД')
    rules = forms.BooleanField(label='Принимаю правила форума')
    legal_versions = forms.CharField(widget=forms.HiddenInput)
    document_kinds = ('consent', 'rules')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.documents = {kind: current_document('consent' if kind == 'binding' else kind)
                          for kind in self.document_kinds}
        self.fields['legal_versions'].initial = signing.dumps(
            {kind: doc.pk if doc else None for kind, doc in self.documents.items()}, salt='legal-versions')
        for kind, doc in self.documents.items():
            field = 'consent' if kind == 'binding' else kind
            self.fields[field].label = format_html('{} — <a href="{}?version={}">{}</a>',
                self.fields[field].label,
                reverse('legal_document', args=['consent' if kind == 'binding' else kind]),
                doc.version if doc else '', 'отдельный документ')

    def clean(self):
        data = super().clean()
        try:
            versions = signing.loads(data.get('legal_versions', ''), salt='legal-versions')
        except signing.BadSignature:
            raise ValidationError('Обновите страницу и ознакомьтесь с документами.')
        if any(doc is None for doc in self.documents.values()) or versions != {
            kind: doc.pk for kind, doc in self.documents.items()
        }:
            raise ValidationError('Документы обновились или недоступны. Обновите страницу и примите текущие версии.')
        return data

    @transaction.atomic
    def record(self, request, user):
        for kind, doc in self.documents.items():
            current = current_document('consent' if kind == 'binding' else kind)
            if current != doc:
                raise ValidationError('Документ изменился. Повторите принятие.')
            ConsentRecord.objects.create(user=user, document=doc, document_type=kind,
                version=doc.version, action='accept', ip=client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:1000])


class BindingConsentForm(ConsentForm):
    document_kinds = ('binding',)
    rules = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop('rules', None)
        self.fields['consent'].label = format_html(
            'Даю отдельное согласие на обработку данных заявки на привязку участка — <a href="{}?version={}">документ</a>',
            reverse('legal_document', args=['consent']),
            self.documents['binding'].version if self.documents['binding'] else '')
