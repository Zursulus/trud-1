from django.db import models
from django.utils import timezone

from .models import Account, ResidentAppeal, User


class SecurityAlert(models.Model):
    KIND_MALWARE = "malware"
    KIND_SUSPICIOUS_FILE = "suspicious_file"
    KIND_SCANNER = "scanner"
    KIND_SPAM = "spam"
    KIND_UPLOAD_QUOTA = "upload_quota"
    KIND_CHOICES = [
        (KIND_MALWARE, "Вредоносный файл"),
        (KIND_SUSPICIOUS_FILE, "Подозрительный файл"),
        (KIND_SCANNER, "Антивирус недоступен"),
        (KIND_SPAM, "Подозрение на спам"),
        (KIND_UPLOAD_QUOTA, "Превышение лимита вложений"),
    ]

    SEVERITY_WARNING = "warning"
    SEVERITY_CRITICAL = "critical"
    SEVERITY_CHOICES = [
        (SEVERITY_WARNING, "Требует проверки"),
        (SEVERITY_CRITICAL, "Критично"),
    ]

    kind = models.CharField("Тип", max_length=32, choices=KIND_CHOICES)
    severity = models.CharField(
        "Уровень", max_length=12, choices=SEVERITY_CHOICES, default=SEVERITY_WARNING,
    )
    actor = models.ForeignKey(
        User, verbose_name="Пользователь", on_delete=models.PROTECT,
        related_name="security_alerts", blank=True, null=True,
    )
    account = models.ForeignKey(
        Account, verbose_name="Лицевой счёт", on_delete=models.PROTECT,
        related_name="security_alerts", blank=True, null=True,
    )
    appeal = models.ForeignKey(
        ResidentAppeal, verbose_name="Обращение", on_delete=models.PROTECT,
        related_name="security_alerts", blank=True, null=True,
    )
    original_name = models.CharField("Имя файла", max_length=255, blank=True)
    file_size = models.PositiveBigIntegerField("Размер файла", blank=True, null=True)
    source_hash = models.CharField("Хэш сетевого источника", max_length=64, blank=True)
    detail = models.CharField("Техническая причина", max_length=300, blank=True)
    fingerprint = models.CharField("Дедупликация", max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField("Обнаружено", default=timezone.now, editable=False)
    resolved_at = models.DateTimeField("Проверено", blank=True, null=True, editable=False)
    resolved_by = models.ForeignKey(
        User, verbose_name="Проверил", on_delete=models.PROTECT,
        related_name="resolved_security_alerts", blank=True, null=True, editable=False,
    )

    class Meta:
        verbose_name = "Сигнал безопасности"
        verbose_name_plural = "Сигналы безопасности"
        ordering = ["resolved_at", "-created_at", "-id"]
        indexes = [
            models.Index(fields=["resolved_at", "created_at"], name="water_sec_alert_state_idx"),
        ]

    @property
    def is_open(self):
        return self.resolved_at is None

    def resolve(self, actor):
        if self.resolved_at is None:
            self.resolved_at = timezone.now()
            self.resolved_by = actor
            self.save(update_fields=["resolved_at", "resolved_by"])
        return self

    def __str__(self):
        return f"{self.get_kind_display()} · {self.created_at:%d.%m.%Y %H:%M}"
