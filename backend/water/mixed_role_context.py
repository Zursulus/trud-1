from .portal_permissions import has_any_portal_access


def mixed_role_context(request):
    user = getattr(request, "user", None)
    return {
        "has_resident_access": bool(
            user
            and user.is_authenticated
            and user.is_staff
            and has_any_portal_access(user)
        ),
    }
