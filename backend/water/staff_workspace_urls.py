from django.urls import path

from . import staff_appeals, staff_workspace


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
    path("accounts/<int:account_id>/", staff_workspace.workspace_account, name="account"),
]
