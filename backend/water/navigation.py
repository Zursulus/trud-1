"""Shared navigation; never grants access or chooses a resident account."""
from django.conf import settings
from django.urls import reverse
from .portal_permissions import CAP_VIEW_ACCOUNT, resolved_accesses
from .board_polls import active_board_membership


def navigation(request):
    user = request.user
    active = bool(user.is_authenticated and user.is_active)
    match = request.resolver_match
    name = (match.url_name or '') if match else ''
    namespace = match.namespace if match else ''
    section = ('management' if namespace in ('staff_workspace', 'admin') or name == 'forum_moderation'
               else 'profile' if namespace == 'two_factor' or name in (
                   'personal_profile', 'profile_email', 'resident_profile', 'resident_security',
                   'resident_password_change', 'profile')
               else 'forum' if namespace == 'forum' else 'cabinet')
    items = [{'label': 'Сайт', 'url': '/', 'key': 'site'}]
    if settings.FORUM_ENABLED and (active or settings.FORUM_PUBLIC_READ):
        items.append({'label': 'Обсуждения', 'url': reverse('forum:index'), 'key': 'forum'})
    if active:
        has_portal = (request._internal_portal_access if hasattr(request, '_internal_portal_access')
                      else bool(resolved_accesses(user, CAP_VIEW_ACCOUNT)))
        if has_portal:
            items.append({'label': 'Мой кабинет', 'url': reverse('resident_plots'), 'key': 'cabinet'})
        elif not user.is_staff:
            # Staff enter through the workspace; a personal cabinet still stays visible.
            items.append({'label': 'Запросить доступ', 'url': reverse('resident_access_request'), 'key': 'access'})
        # Kept independent of forum and plot access.
        items.append({'label': 'Профиль', 'url': reverse('personal_profile'), 'key': 'profile'})
        membership = (request._internal_board_membership if hasattr(request, '_internal_board_membership')
                      else active_board_membership(user))
        if membership:
            items.append({'label': 'Опросы правления', 'url': reverse('board_poll_home'), 'key': 'board'})
        if user.is_staff:
            items.append({'label': 'Управление', 'url': reverse('staff_workspace:home'), 'key': 'management'})
    else:
        items.extend([
            {'label': 'Вход', 'url': reverse('resident_login'), 'key': 'login'},
            {'label': 'Помощь', 'url': reverse('resident_access_help'), 'key': 'help'},
        ])
    if name in ('resident_login', 'login', 'request_login_code', 'account_login'):
        section = 'login'
    if name == 'resident_access_help':
        section = 'help'
    if name.startswith('board_'):
        section = 'board'
    if name.startswith('resident_access_request'):
        section = 'access'
    current = next((item for item in items if item['key'] == section), None)
    trail = []
    if namespace == 'staff_workspace' and name != 'home':
        parent = (name or '').split('_')[0]
        sections = {
            'water': ('Учёт воды', 'water'), 'account': ('Участки и счета', 'accounts'),
            'accounts': ('Участки и счета', 'accounts'), 'plot': ('Участки и счета', 'accounts'),
            'search': ('Поиск', 'search'), 'appeal': ('Обращения', 'appeals'),
            'appeals': ('Обращения', 'appeals'), 'finance': ('Начисления и оплаты', 'finance'),
            'access': ('Доступы жителей', 'access'), 'documents': ('Сайт и документы', 'documents'),
            'site': ('Сайт и документы', 'documents'), 'news': ('Сайт и документы', 'documents'),
            'public': ('Сайт и документы', 'documents'),
            'governance': ('Опросы правления', 'governance'),
            'security': ('Сигналы безопасности', 'security'), 'more': ('Ещё', 'more'),
        }
        label, route = sections.get(parent, ('Рабочие разделы', 'home'))
        trail.append({'label': label, 'url': reverse('staff_workspace:' + route)})
    elif name == 'profile_email':
        trail.append({'label': 'Почта', 'url': reverse('profile_email')})
    elif name == 'resident_password_change':
        trail.append({'label': 'Смена пароля', 'url': reverse('resident_password_change')})
    return {'internal_nav': items, 'internal_active': section,
            'internal_current': current, 'internal_trail': trail}


def management_links(user, *, request=None, workspace=False):
    if not (user.is_authenticated and user.is_active and user.is_staff):
        return []
    from django.contrib import admin
    from django.http import HttpRequest
    from .models import ImportBatch
    from .staff_workspace import _capabilities
    from .staff_workbench import can_use_workbench
    from forum.access import can_moderate
    capabilities = _capabilities(user)
    permission_request = request if request is not None else HttpRequest()
    permission_request.user = user
    import_admin = admin.site._registry[ImportBatch]
    imports_visible = import_admin.has_module_permission(permission_request)
    entries = [
        ('Жители и участки', 'staff_workspace:workbench', can_use_workbench(user)),
        ('Найти участок или счёт', 'staff_workspace:search', capabilities['can_view_accounts']),
        ('Участки и счета', 'staff_workspace:accounts', capabilities['can_view_accounts']),
        ('Доступы жителей', 'staff_workspace:access', capabilities['can_use_access_workspace']),
        ('Мои показания и проверки', 'staff_workspace:water_line', capabilities['can_use_controller_workspace']),
        ('Учёт воды', 'staff_workspace:water', capabilities['can_view_water'] and not capabilities['can_use_controller_workspace']),
        ('Начисления и оплаты', 'staff_workspace:finance', capabilities['can_use_finance_workspace']),
        ('Обращения', 'staff_workspace:appeals', capabilities['can_view_appeals']),
        ('Сайт и документы', 'staff_workspace:documents', capabilities['can_view_documents']),
        ('Опросы правления', 'staff_workspace:governance', capabilities['can_view_governance']),
        ('Сигналы безопасности', 'staff_workspace:security', capabilities['can_view_security_alerts']),
        ('Модерация форума', 'forum_moderation', settings.FORUM_ENABLED and can_moderate(user)),
        ('Разделы форума', 'admin:forum_section_changelist', settings.FORUM_ENABLED and (
            user.has_perm('forum.view_section') or user.has_perm('forum.change_section'))),
        ('Создать раздел форума', 'admin:forum_section_add', settings.FORUM_ENABLED and user.has_perm('forum.add_section')),
        ('Пользователи', 'admin:water_user_changelist', user.is_superuser),
        ('Группы и служебные права', 'admin:auth_group_changelist', user.is_superuser),
        ('Импорт', 'admin:water_importbatch_changelist', imports_visible and import_admin.has_view_permission(permission_request)),
        ('Добавить импорт', 'admin:water_importbatch_upload', imports_visible and user.has_perm('water.add_importbatch')),
        ('Роли и полномочия', 'staff_workspace:roles_guide', True),
        ('Техническая админка', 'admin:index', True),
    ]
    # All workspace destinations and forum moderation are already in the sidebar
    # or the mobile section index. Keep the full catalog for other callers.
    return [{'label': label, 'url': reverse(route)} for label, route, allowed in entries
            if allowed and (not workspace or (not route.startswith('staff_workspace:')
                                               and route != 'forum_moderation'))]
