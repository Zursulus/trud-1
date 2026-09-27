from django.core.exceptions import ValidationError
from django.utils import timezone


PUBLICATION_CONFIRMATION_ERROR = (
    "Перед публикацией проверьте материал и подтвердите отсутствие закрытых данных."
)


def apply_publication_state(obj, *, actor, is_published, confirmed):
    """Apply the single public/private publication boundary to one content object.

    Saving draft content is allowed without confirmation. Every published save
    requires an explicit privacy confirmation and records who approved that
    concrete published version.
    """
    if is_published and not confirmed:
        raise ValidationError(PUBLICATION_CONFIRMATION_ERROR)

    obj.is_published = bool(is_published)
    if obj.is_published:
        obj.public_checked = True
        obj.published_at = timezone.now()
        obj.published_by = actor
    else:
        obj.public_checked = False
        obj.published_at = None
        obj.published_by = None
    return obj
