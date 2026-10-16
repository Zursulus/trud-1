from django.contrib import admin
from django.contrib.admin import AdminSite
from django.contrib.auth import REDIRECT_FIELD_NAME, views as auth_views
from django.contrib.auth.views import redirect_to_login
from django.shortcuts import resolve_url
from django.urls import include, path
from django.utils.http import url_has_allowed_host_and_scheme
from two_factor.admin import original_login
from two_factor.utils import default_device
from two_factor.urls import urlpatterns as two_factor_urls
from config.status import deployment_status
from public_site import views as public_views
from forum import views as forum_views
from water import (
    access_request_views,
    appeal_admin_tools,
    board_poll_views,
    board_portal,
    controller_workspace,
    portal,
    portal_ui,
    password_admin,
    resident_actions,
)
from water.management.commands.setup_roles import ADMIN, LEGACY_ADMIN
from water.balance import water_balance_view
from water.package_views import package_dry_run
from water.reading_admin_tools import reassign_reading_view
from water.reading_review_config import export_readings_xlsx, reading_review_view


class MainAdminSite(AdminSite):
    """Require a verified OTP session only for TSN administrators/superusers."""

    def requires_otp(self, request):
        # Admin wrappers and template context may check access more than once.
        # Cache only within this request so role changes affect the next request.
        if not hasattr(request, '_admin_mfa_required'):
            user = request.user
            request._admin_mfa_required = user.is_superuser or user.groups.filter(
                name__in=(ADMIN, LEGACY_ADMIN),
            ).exists()
        return request._admin_mfa_required

    def has_permission(self, request):
        return super().has_permission(request) and (
            request.user.is_verified() or not self.requires_otp(request)
        )

    def login(self, request, extra_context=None):
        if (super().has_permission(request) and not request.user.is_verified()
                and self.requires_otp(request)):
            redirect_to = request.POST.get(REDIRECT_FIELD_NAME, request.GET.get(REDIRECT_FIELD_NAME))
            if not redirect_to or not url_has_allowed_host_and_scheme(
                redirect_to, allowed_hosts={request.get_host()}, require_https=request.is_secure(),
            ):
                redirect_to = resolve_url('admin:index')
            destination = 'two_factor:login' if default_device(request.user) else 'two_factor:setup'
            return redirect_to_login(redirect_to, resolve_url(destination))
        return original_login(self, request, extra_context)


admin.site.__class__ = MainAdminSite

urlpatterns = [
    path('admin/cabinet/accounts/', include('forum.auth_urls')),
    path('admin/cabinet/forum/', include('forum.urls')),
    path('work/forum/', forum_views.workspace_moderation, name='forum_moderation'),
    path('work/', include('water.staff_workspace_urls')),
    path('admin/deployment-status/', deployment_status, name='deployment_status'),
    path('admin/water/passwords/', admin.site.admin_view(password_admin.password_access_admin), name='water_password_access_admin'),
    path('admin/public/announcement/<int:announcement_id>/image/', public_views.public_announcement_image, name='public_announcement_image'),
    path('admin/public/content/', public_views.public_content, name='public_content'),
    path('admin/public/document/<int:document_id>/', public_views.public_document_download, name='public_document_download'),
    path('admin/cabinet/login/', auth_views.LoginView.as_view(
        template_name='water/portal/login.html', authentication_form=portal.ResidentAuthenticationForm,
        redirect_authenticated_user=True, next_page='resident_dashboard',
    ), name='resident_login'),
    path('admin/cabinet/logout/', auth_views.LogoutView.as_view(next_page='resident_login'), name='resident_logout'),
    path('admin/cabinet/help/', portal_ui.access_help, name='resident_access_help'),
    path('admin/cabinet/request-access/', access_request_views.request_access, name='resident_access_request'),
    path('admin/cabinet/request-access/sent/', access_request_views.request_access_sent, name='resident_access_request_sent'),
    path('admin/cabinet/invite/<str:token>/', portal.register_invite, name='resident_invite'),
    path('admin/cabinet/reset/<str:token>/', portal.reset_password, name='resident_password_reset'),
    path('admin/cabinet/password/', portal.change_password, name='resident_password_change'),
    path('admin/cabinet/board/', board_poll_views.board_home, name='board_poll_home'),
    path('admin/cabinet/board/poll/<int:poll_id>/', board_poll_views.board_poll_detail, name='board_poll_detail'),
    path(
        'admin/cabinet/board/poll/<int:poll_id>/protocol/',
        board_poll_views.board_protocol_download,
        name='board_poll_protocol',
    ),
    path('admin/cabinet/', board_portal.dashboard, name='resident_dashboard'),
    path('admin/cabinet/plots/', portal_ui.plots, name='resident_plots'),
    path('admin/cabinet/account/<int:account_id>/', board_portal.account_home, name='resident_account'),
    path('admin/cabinet/account/<int:account_id>/payments/', portal_ui.payments, name='resident_payments'),
    path('admin/cabinet/account/<int:account_id>/water/', portal_ui.water, name='resident_water'),
    path('admin/cabinet/account/<int:account_id>/appeals/', portal_ui.appeals, name='resident_appeals'),
    path('admin/cabinet/account/<int:account_id>/documents/', portal_ui.documents, name='resident_documents'),
    path('admin/cabinet/account/<int:account_id>/notifications/', portal_ui.notifications, name='resident_notifications'),
    path('admin/cabinet/account/<int:account_id>/more/', board_portal.more, name='resident_more'),
    path('admin/cabinet/account/<int:account_id>/profile/', portal_ui.profile, name='resident_profile'),
    path('admin/cabinet/account/<int:account_id>/security/', portal_ui.security, name='resident_security'),
    path('admin/cabinet/account/<int:account_id>/meter/<int:meter_id>/reading/', portal.submit_reading, name='resident_reading'),
    path('admin/cabinet/account/<int:account_id>/appeal/new/', resident_actions.create_appeal, name='resident_appeal_new'),
    path('admin/cabinet/account/<int:account_id>/appeal/<int:appeal_id>/', resident_actions.resident_appeal, name='resident_appeal'),
    path(
        'admin/cabinet/account/<int:account_id>/appeal/<int:appeal_id>/attachment/<int:attachment_id>/',
        resident_actions.download_appeal_attachment,
        name='resident_appeal_attachment',
    ),
    path('admin/cabinet/account/<int:account_id>/document/<int:document_id>/', portal.download_document, name='resident_document'),
    path(
        'admin/water/residentappeal/<int:appeal_id>/attachments/',
        admin.site.admin_view(appeal_admin_tools.manage_appeal_attachments),
        name='admin_appeal_attachments',
    ),
    path(
        'admin/water/residentappeal/attachment/<int:attachment_id>/',
        admin.site.admin_view(appeal_admin_tools.download_appeal_attachment),
        name='admin_appeal_attachment_download',
    ),
    path('admin/water/balance/', admin.site.admin_view(water_balance_view), name='water_balance'),
    path(
        'admin/water/controller-workspace/',
        admin.site.admin_view(controller_workspace.controller_workspace),
        name='water_controller_workspace',
    ),
    path('admin/water/package-dry-run/', admin.site.admin_view(package_dry_run), name='water_package_dry_run'),
    path('admin/water/readings/review/', admin.site.admin_view(reading_review_view), name='water_readings_review'),
    path('admin/water/readings/export-xlsx/', admin.site.admin_view(export_readings_xlsx), name='water_readings_xlsx'),
    path(
        'admin/water/readings/<int:reading_id>/reassign/',
        admin.site.admin_view(reassign_reading_view),
        name='water_reading_reassign',
    ),
    path('admin/', include(two_factor_urls)),
    path('admin/', admin.site.urls),
]
