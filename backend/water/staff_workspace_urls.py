from django.urls import path

from . import staff_access, staff_appeals, staff_finance, staff_more, staff_security, staff_workspace


app_name = "staff_workspace"

urlpatterns = [
    path("", staff_workspace.workspace_dashboard, name="home"),
    path("search/", staff_workspace.workspace_search, name="search"),
    path("accounts/", staff_workspace.workspace_accounts, name="accounts"),
    path("water/", staff_workspace.workspace_water, name="water"),
    path("appeals/", staff_appeals.workspace_appeals, name="appeals"),
    path("appeals/<int:appeal_id>/", staff_appeals.workspace_appeal, name="appeal"),
    path(
        "appeals/attachment/<int:attachment_id>/",
        staff_appeals.workspace_appeal_attachment,
        name="appeal_attachment",
    ),
    path("finance/", staff_finance.workspace_finance, name="finance"),
    path("finance/periods/<int:period_id>/", staff_finance.workspace_finance_period, name="finance_period"),
    path("finance/payments/", staff_finance.workspace_finance_payments, name="finance_payments"),
    path("finance/payments/new/", staff_finance.workspace_finance_payment_create, name="finance_payment_create"),
    path("finance/payments/<int:payment_id>/", staff_finance.workspace_finance_payment, name="finance_payment"),
    path("finance/accounts/<int:account_id>/", staff_finance.workspace_finance_account, name="finance_account"),
    path("access/", staff_access.workspace_access, name="access"),
    path("access/requests/<int:request_id>/", staff_access.workspace_access_request, name="access_request"),
    path("access/accesses/<int:access_id>/", staff_access.workspace_access_detail, name="access_detail"),
    path(
        "access/invites/<int:invite_id>/revoke/",
        staff_access.workspace_access_revoke_invite,
        name="access_revoke_invite",
    ),
    path(
        "access/resets/<int:reset_id>/revoke/",
        staff_access.workspace_access_revoke_reset,
        name="access_revoke_reset",
    ),
    path("security/", staff_security.workspace_security, name="security"),
    path("more/", staff_more.workspace_more, name="more"),
    path("accounts/<int:account_id>/", staff_workspace.workspace_account, name="account"),
]
