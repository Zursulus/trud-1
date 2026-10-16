from public_site.legal import current_document
from .legal import ConsentForm, BindingConsentForm
from .models import ConsentRecord


def consent_payload(binding=False):
    form = BindingConsentForm() if binding else ConsentForm()
    result = {'consent': 'on', 'legal_versions': form['legal_versions'].value()}
    if not binding:
        result['rules'] = 'on'
    return result


def record_consents(user, kinds=('consent', 'rules')):
    for kind in kinds:
        doc = current_document('consent' if kind == 'binding' else kind)
        ConsentRecord.objects.create(user=user, document=doc, document_type=kind,
                                     version=doc.version, action='accept')
