from django.urls import path

from . import staff_workspace


app_name = "staff_workspace"

urlpatterns = [
    path("", staff_workspace.workspace_dashboard, name="home"),
    path("search/", staff_workspace.workspace_search, name="search"),
    path("accounts/", staff_workspace.workspace_accounts, name="accounts"),
    path("accounts/<int:account_id>/", staff_workspace.workspace_account, name="account"),
]
