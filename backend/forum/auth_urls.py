from allauth.account import views
from django.urls import path
from django.views.generic import RedirectView

from .auth import email_login_enabled

# Expose only the needed allauth flows; staff email/password management stays separate.
urlpatterns = [
    path('login/', email_login_enabled(views.request_login_code), name='account_login'),
    path('login/code/', email_login_enabled(views.request_login_code), name='account_request_login_code'),
    path('login/code/confirm/', email_login_enabled(views.confirm_login_code), name='account_confirm_login_code'),
    # Stock allauth returns here after an expired/already completed verification.
    path('email/', email_login_enabled(RedirectView.as_view(pattern_name='account_login')), name='account_email'),
    path('signup/', email_login_enabled(views.signup), name='account_signup'),
    path('confirm-email/', email_login_enabled(views.email_verification_sent), name='account_email_verification_sent'),
    path('logout/', email_login_enabled(views.logout), name='account_logout'),
    path('inactive/', email_login_enabled(views.account_inactive), name='account_inactive'),
]
