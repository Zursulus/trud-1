from django.contrib.staticfiles.storage import StaticFilesStorage


class PublicStaticFilesStorage(StaticFilesStorage):
    """Public static assets must remain readable by nginx.

    Private uploaded files keep the stricter FILE_UPLOAD_* permissions from
    settings.py; collectstatic uses this storage with separate public modes.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('file_permissions_mode', 0o644)
        kwargs.setdefault('directory_permissions_mode', 0o755)
        super().__init__(*args, **kwargs)
