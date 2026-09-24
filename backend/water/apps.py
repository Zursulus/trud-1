from django.apps import AppConfig


class WaterConfig(AppConfig):
    name = 'water'
    verbose_name = 'Труд-1 · реестр и вода'

    def ready(self):
        # Дополнительные модели кабинета вынесены отдельно, чтобы не раздувать
        # исторический models.py. Импорт здесь регистрирует их в приложении.
        from . import resident_models  # noqa: F401
        from . import resident_numbers  # noqa: F401
        from . import private_registry  # noqa: F401
        from . import access_requests  # noqa: F401
        from . import portal_permissions  # noqa: F401
        # Рабочая админка должна показывать сначала несколько понятных сценариев,
        # а не полный технический список моделей. Полная структура остаётся
        # доступной в сворачиваемом служебном блоке на нашей index-странице.
        from django.contrib import admin

        admin.site.index_template = 'admin/water/index.html'
        admin.site.enable_nav_sidebar = False

        # AdminConfig performs autodiscovery before WaterConfig.ready() in the
        # configured application order. Apply the PII boundary only after the
        # original ModelAdmin classes have been registered.
        from . import privacy_admin  # noqa: F401
        from . import access_request_admin  # noqa: F401
        from . import portal_permissions_admin  # noqa: F401
