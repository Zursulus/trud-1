from django.apps import AppConfig


class WaterConfig(AppConfig):
    name = 'water'
    verbose_name = 'Труд-1 · реестр и вода'

    def ready(self):
        # Рабочая админка должна показывать сначала несколько понятных сценариев,
        # а не полный технический список моделей. Полная структура остаётся
        # доступной в сворачиваемом служебном блоке на нашей index-странице.
        from django.contrib import admin

        admin.site.index_template = 'admin/water/index.html'
        admin.site.enable_nav_sidebar = False
