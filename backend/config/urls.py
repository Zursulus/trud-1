from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from two_factor.admin import AdminSiteOTPRequired
from two_factor.urls import urlpatterns as two_factor_urls
from water import portal

# Keep existing registrations but require an OTP-verified session for every
# admin view. two_factor also patches the old admin login route to this flow.
admin.site.__class__ = AdminSiteOTPRequired

urlpatterns = [
    # Resident pages stay under the already proxied /admin/ prefix, but are
    # separate from the staff admin and never weaken its OTP requirement.
    path('admin/cabinet/login/', auth_views.LoginView.as_view(
        template_name='water/portal/login.html', authentication_form=portal.ResidentAuthenticationForm,
        redirect_authenticated_user=True, next_page='resident_dashboard',
    ), name='resident_login'),
    path('admin/cabinet/logout/', auth_views.LogoutView.as_view(next_page='resident_login'), name='resident_logout'),
    path('admin/cabinet/invite/<str:token>/', portal.register_invite, name='resident_invite'),
    path('admin/cabinet/', portal.dashboard, name='resident_dashboard'),
    path('admin/cabinet/account/<int:account_id>/', portal.resident_account, name='resident_account'),
    path('admin/cabinet/account/<int:account_id>/meter/<int:meter_id>/reading/', portal.submit_reading, name='resident_reading'),
    # Keep every staff authentication page below /admin/: production Nginx
    # proxies that prefix to Django while the public root stays static.
    path('admin/', include(two_factor_urls)),
    path('admin/', admin.site.urls),
]
