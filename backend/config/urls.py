from django.contrib import admin
from django.urls import include, path
from two_factor.admin import AdminSiteOTPRequired
from two_factor.urls import urlpatterns as two_factor_urls

# Keep existing registrations but require an OTP-verified session for every
# admin view. two_factor also patches the old admin login route to this flow.
admin.site.__class__ = AdminSiteOTPRequired

urlpatterns = [
    # Keep every staff authentication page below /admin/: production Nginx
    # proxies that prefix to Django while the public root stays static.
    path('admin/', include(two_factor_urls)),
    path('admin/', admin.site.urls),
]
