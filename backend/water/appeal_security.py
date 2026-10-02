[Reading 457 lines from start (total: 457 lines, 0 remaining)]

from datetime import timedelta
import hashlib
import logging
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

from django.conf import settings
from django.core.cache import cache
from django.core.checks import Error, Tags, register
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.db.models.signals import pre_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.crypto import salted_hmac

from config.network import client_ip
from . import resident_models
from .models import ResidentAppeal, ResidentAppealMessage
from .resident_models import ResidentAppealAttachment
from .security_models import SecurityAlert


logger = logging.getLogger("water.security")

APPEAL_ATTACHMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".docx", ".xlsx"}
APPEAL_ATTACHMENT_HELP = "PDF, JPG, PNG, DOCX или XLSX до 10 МБ."
APPEAL_ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
OFFICE_MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
OFFICE_MAX_MEMBER_BYTES = 50 * 1024 * 1024
OFFICE_MAX_MEMBERS = 2000

DANGEROUS_EXTENSIONS = {
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jar", ".msi", ".scr", ".dll",
    ".docm", ".xlsm", ".pptm", ".zip", ".rar", ".7z", ".html", ".htm", ".svg",
}
SECURITY_REJECTION_CODES = {
    "appeal_dangerous_extension",
    "appeal_content_mismatch",
    "appeal_dangerous_office",
    "appeal_zip_bomb",
    "appeal_malware",
    "appeal_scanner_unavailable",
}

# ResidentAppealAttachment.clean() intentionally keeps the final model-level
# extension allowlist. Mutating the existing set preserves all imports of it.
resident_models.APPEAL_ATTACHMENT_EXTENSIONS.clear()
resident_models.APPEAL_ATTACHMENT_EXTENSIONS.update(APPEAL_ATTACHMENT_EXTENSIONS)


def _setting_int(name, default):
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _file_object(upload):
    return getattr(upload, "file", upload)


def _rewind(upload):
    fileobj = _file_object(upload)
    try:
        fileobj.seek(0)
    except (AttributeError, OSError):
        try:
            upload.open("rb")
        except (AttributeError, OSError):
            pass
        fileobj = _file_object(upload)
        fileobj.seek(0)
    return fileobj


def _safe_name(upload):
    raw = Path(getattr(upload, "name", "") or "").name
    return "".join(ch for ch in raw if ch.isprintable())[:255]


def _raise(message, code):
    raise ValidationError(message, code=code)


def _verify_pdf(fileobj):
    fileobj.seek(0)
    data = fileobj.read(APPEAL_ATTACHMENT_MAX_BYTES + 1)
    fileobj.seek(0)
    if not data.startswith(b"%PDF-"):
        _raise("Содержимое файла не соответствует PDF.", "appeal_content_mismatch")
    lowered = data.lower()
    if b"/javascript" in lowered or b"/launch" in lowered:
        _raise("PDF содержит активное содержимое и не может быть принят.", "appeal_dangerous_office")


def _verify_image(fileobj, suffix):
    fileobj.seek(0)
    head = fileobj.read(16)
    fileobj.seek(0)
    if suffix == ".png" and not head.startswith(b"\x89PNG\r\n\x1a\n"):
        _raise("Содержимое файла не соответствует PNG.", "appeal_content_mismatch")
    if suffix in {".jpg", ".jpeg"} and not head.startswith(b"\xff\xd8\xff"):
        _raise("Содержимое файла не соответствует JPEG.", "appeal_content_mismatch")


def _verify_office(fileobj, suffix):
    fileobj.seek(0)
    try:
        archive = zipfile.ZipFile(fileobj)
    except (zipfile.BadZipFile, OSError):
        fileobj.seek(0)
        _raise("Содержимое файла не соответствует формату Office.", "appeal_content_mismatch")

    try:
        infos = archive.infolist()
        if len(infos) > OFFICE_MAX_MEMBERS:
            _raise("Office-файл имеет подозрительную структуру.", "appeal_zip_bomb")
        total = 0
        names = set()
        for info in infos:
            normalized = info.filename.replace("\\", "/").lstrip("/")
            lowered = normalized.lower()
            names.add(lowered)
            if ".." in Path(normalized).parts or info.flag_bits & 0x1:
                _raise("Защищённые или некорректные Office-файлы не принимаются.", "appeal_dangerous_office")
            if info.file_size > OFFICE_MAX_MEMBER_BYTES:
                _raise("Office-файл имеет подозрительно большой внутренний объект.", "appeal_zip_bomb")
            total += info.file_size
            if total > OFFICE_MAX_UNCOMPRESSED_BYTES:
                _raise("Office-файл имеет подозрительно большой распакованный размер.", "appeal_zip_bomb")

        required = {
            ".docx": {"[content_types].xml", "_rels/.rels", "word/document.xml"},
            ".xlsx": {"[content_types].xml", "_rels/.rels", "xl/workbook.xml"},
        }[suffix]
        if not required.issubset(names):
            _raise("Содержимое файла не соответствует заявленному формату Office.", "appeal_content_mismatch")

        dangerous_markers = (
            "vbaproject.bin", "vbadata.xml", "/embeddings/", "/activex/", "customui/",
        )
        if any(any(marker in name for marker in dangerous_markers) for name in names):
            _raise("Office-файл содержит макросы или встроенные активные объекты.", "appeal_dangerous_office")
    finally:
        archive.close()
        fileobj.seek(0)


def _scanner_path():
    configured = getattr(settings, "APPEAL_CLAMDSCAN_PATH", "/usr/bin/clamdscan")
    if not configured:
        return None
    path = Path(configured)
    if path.is_absolute():
        return str(path) if path.is_file() else None
    return shutil.which(configured)


def _scan_malware(fileobj):
    required = bool(getattr(settings, "APPEAL_MALWARE_SCAN_REQUIRED", not settings.DEBUG))
    scanner = _scanner_path()
    if not scanner:
        if required:
            _raise(
                "Проверка файла временно недоступна. Попробуйте позже.",
                "appeal_scanner_unavailable",
            )
        return

    timeout = _setting_int("APPEAL_CLAMDSCAN_TIMEOUT", 20)
    fileobj.seek(0)
    result = None
    try:
        with tempfile.NamedTemporaryFile(prefix="trud-appeal-", suffix=".upload") as temporary:
            while True:
                chunk = fileobj.read(1024 * 1024)
                if not chunk:
                    break
                temporary.write(chunk)
            temporary.flush()
            # Do not use --fdpass here: it only works with a local Unix socket.
            # Without it clamdscan automatically streams when needed, which also
            # supports a clamd reached over a private TCP tunnel.
            command = [scanner, "--no-summary", temporary.name]
            try:
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                if required:
                    _raise(
                        "Проверка файла временно недоступна. Попробуйте позже.",
                        "appeal_scanner_unavailable",
                    )
                return
    finally:
        fileobj.seek(0)

    if result is None:
        return
    if result.returncode == 1:
        _raise("Файл отклонён системой безопасности.", "appeal_malware")
    if result.returncode != 0 and required:
        _raise(
            "Проверка файла временно недоступна. Попробуйте позже.",
            "appeal_scanner_unavailable",
        )


def validate_appeal_attachment(upload):
    """Validate and scan one upload before it can reach private storage."""
    if not upload:
        return upload
    if getattr(upload, "_trud_appeal_security_validated", False):
        return upload

    size = int(getattr(upload, "size", 0) or 0)
    if size > APPEAL_ATTACHMENT_MAX_BYTES:
        _raise("Файл должен быть не больше 10 МБ.", "appeal_file_too_large")

    suffix = Path(getattr(upload, "name", "") or "").suffix.lower()
    if suffix not in APPEAL_ATTACHMENT_EXTENSIONS:
        if suffix in DANGEROUS_EXTENSIONS:
            _raise("Этот тип файла запрещён из соображений безопасности.", "appeal_dangerous_extension")
        _raise(f"Разрешены только {APPEAL_ATTACHMENT_HELP}", "appeal_unsupported_extension")

    fileobj = _rewind(upload)
    if suffix == ".pdf":
        _verify_pdf(fileobj)
    elif suffix in {".jpg", ".jpeg", ".png"}:
        _verify_image(fileobj, suffix)
    else:
        _verify_office(fileobj, suffix)
    _scan_malware(fileobj)

    try:
        upload._trud_appeal_security_validated = True
        fileobj._trud_appeal_security_validated = True
    except (AttributeError, TypeError):
        pass
    fileobj.seek(0)
    return upload


@receiver(pre_save, sender=ResidentAppealAttachment)
def validate_attachment_before_storage(sender, instance, **kwargs):
    if not instance.document:
        return
    fileobj = _file_object(instance.document)
    if getattr(fileobj, "_trud_appeal_security_validated", False):
        return
    validate_appeal_attachment(fileobj)


def _source_hash(request):
    address = client_ip(request) if request is not None else None
    if not address:
        return ""
    return salted_hmac("trud-appeal-security-source", address).hexdigest()


def _alert_fingerprint(kind, actor_id, account_id, appeal_id, detail, now):
    bucket = int(now.timestamp() // (15 * 60))
    raw = f"{kind}|{actor_id or 0}|{account_id or 0}|{appeal_id or 0}|{detail}|{bucket}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_security_alert(*, kind, severity, request=None, actor=None, account=None, appeal=None,
                          upload=None, detail=""):
    now = timezone.now()
    actor_id = getattr(actor, "pk", None)
    account_id = getattr(account, "pk", account) if account is not None else None
    appeal_id = getattr(appeal, "pk", appeal) if appeal is not None else None
    fingerprint = _alert_fingerprint(kind, actor_id, account_id, appeal_id, detail, now)
    defaults = {
        "kind": kind,
        "severity": severity,
        "actor": actor if getattr(actor, "pk", None) else None,
        "account_id": account_id,
        "appeal_id": appeal_id,
        "original_name": _safe_name(upload) if upload else "",
        "file_size": int(getattr(upload, "size", 0) or 0) if upload else None,
        "source_hash": _source_hash(request),
        "detail": str(detail)[:300],
    }
    alert, created = SecurityAlert.objects.get_or_create(fingerprint=fingerprint, defaults=defaults)
    if created:
        level = logging.ERROR if severity == SecurityAlert.SEVERITY_CRITICAL else logging.WARNING
        logger.log(
            level,
            "security_alert kind=%s alert_id=%s actor_id=%s account_id=%s appeal_id=%s detail=%s",
            kind, alert.pk, actor_id, account_id, appeal_id, detail,
        )
    return alert


def record_form_upload_rejection(*, form, field_name, request, actor, account=None, appeal=None):
    codes = {error.code for error in form.errors.as_data().get(field_name, []) if error.code}
    relevant = codes & SECURITY_REJECTION_CODES
    if not relevant:
        return None
    code = sorted(relevant)[0]
    if code == "appeal_malware":
        kind = SecurityAlert.KIND_MALWARE
        severity = SecurityAlert.SEVERITY_CRITICAL
    elif code == "appeal_scanner_unavailable":
        kind = SecurityAlert.KIND_SCANNER
        severity = SecurityAlert.SEVERITY_CRITICAL
    else:
        kind = SecurityAlert.KIND_SUSPICIOUS_FILE
        severity = SecurityAlert.SEVERITY_WARNING
    return record_security_alert(
        kind=kind,
        severity=severity,
        request=request,
        actor=actor,
        account=account,
        appeal=appeal,
        upload=request.FILES.get(field_name),
        detail=code,
    )


def register_resident_submission_attempt(request, user):
    """Cheap burst protection that also counts rejected/invalid POST attempts."""
    source = _source_hash(request)[:16] or "unknown"
    key = f"trud:appeal-attempt:{user.pk}:{source}"
    timeout = 60
    cache.add(key, 0, timeout=timeout)
    try:
        attempts = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=timeout)
        attempts = 1
    limit = _setting_int("APPEAL_POST_ATTEMPTS_PER_MINUTE", 12)
    if attempts > limit:
        record_security_alert(
            kind=SecurityAlert.KIND_SPAM,
            severity=SecurityAlert.SEVERITY_WARNING,
            request=request,
            actor=user,
            detail="burst_post_attempts",
        )
        raise ValidationError(
            "Слишком много отправок подряд. Повторите через минуту.",
            code="appeal_rate_limited",
        )


def enforce_resident_submission_limits(*, request, user, account, appeal=None, upload=None, creating=False):
    """Database-backed limits for successful traffic and attachment volume."""
    now = timezone.now()
    minute_start = now - timedelta(minutes=1)
    recent = ResidentAppeal.objects.filter(author=user, opened_at__gte=minute_start).count()
    recent += ResidentAppealMessage.objects.filter(author=user, created_at__gte=minute_start).count()
    message_limit = _setting_int("APPEAL_MESSAGES_PER_MINUTE", 5)
    if recent >= message_limit:
        record_security_alert(
            kind=SecurityAlert.KIND_SPAM,
            severity=SecurityAlert.SEVERITY_WARNING,
            request=request,
            actor=user,
            account=account,
            appeal=appeal,
            detail="message_rate_limit",
        )
        raise ValidationError(
            "Слишком много сообщений подряд. Повторите через минуту.",
            code="appeal_rate_limited",
        )

    if creating:
        creation_start = now - timedelta(minutes=10)
        new_count = ResidentAppeal.objects.filter(author=user, opened_at__gte=creation_start).count()
        new_limit = _setting_int("APPEAL_NEW_PER_TEN_MINUTES", 3)
        if new_count >= new_limit:
            record_security_alert(
                kind=SecurityAlert.KIND_SPAM,
                severity=SecurityAlert.SEVERITY_WARNING,
                request=request,
                actor=user,
                account=account,
                detail="new_appeal_rate_limit",
            )
            raise ValidationError(
                "Слишком много новых обращений подряд. Продолжите позже или дополните уже созданное обращение.",
                code="appeal_rate_limited",
            )

    if not upload:
        return

    size = int(getattr(upload, "size", 0) or 0)
    local_now = timezone.localtime(now)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    used_today = ResidentAppealAttachment.objects.filter(
        uploaded_by=user, created_at__gte=day_start,
    ).aggregate(total=Sum("file_size"))["total"] or 0
    daily_limit = _setting_int("APPEAL_DAILY_UPLOAD_BYTES", 50 * 1024 * 1024)
    if used_today + size > daily_limit:
        record_security_alert(
            kind=SecurityAlert.KIND_UPLOAD_QUOTA,
            severity=SecurityAlert.SEVERITY_WARNING,
            request=request,
            actor=user,
            account=account,
            appeal=appeal,
            upload=upload,
            detail="daily_upload_quota",
        )
        raise ValidationError(
            "Дневной лимит вложений исчерпан. Попробуйте позже или свяжитесь с правлением.",
            code="appeal_upload_quota",
        )

    if appeal is not None:
        appeal_used = ResidentAppealAttachment.objects.filter(appeal=appeal).aggregate(
            total=Sum("file_size")
        )["total"] or 0
        appeal_limit = _setting_int("APPEAL_TOTAL_UPLOAD_BYTES", 100 * 1024 * 1024)
        if appeal_used + size > appeal_limit:
            record_security_alert(
                kind=SecurityAlert.KIND_UPLOAD_QUOTA,
                severity=SecurityAlert.SEVERITY_WARNING,
                request=request,
                actor=user,
                account=account,
                appeal=appeal,
                upload=upload,
                detail="appeal_upload_quota",
            )
            raise ValidationError(
                "Для этого обращения достигнут лимит вложений. Создайте новое обращение только при необходимости.",
                code="appeal_upload_quota",
            )


@register(Tags.security, deploy=True)
def appeal_malware_scanner_check(app_configs, **kwargs):
    if not bool(getattr(settings, "APPEAL_MALWARE_SCAN_REQUIRED", not settings.DEBUG)):
        return []
    if _scanner_path():
        return []
    return [Error(
        "Для production-вложений требуется clamdscan, но исполняемый файл не найден.",
        id="water.E901",
        hint="Установите clamav-daemon/clamdscan или задайте APPEAL_CLAMDSCAN_PATH до deploy.",
    )]

[executed on device: sandbox (2ce8fd8f-c8b1-4737-95b3-20fa4189189e)]