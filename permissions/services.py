def user_has_permission(user, permission_code):
    """Return True when a user has an allowed Hesba role permission.

    This helper is intentionally small at the foundation stage. Future UI/API
    layers should call this before executing protected actions.
    """

    if user is None or not getattr(user, "is_authenticated", False):
        return False

    if getattr(user, "is_superuser", False):
        return True

    profile = getattr(user, "hesba_profile", None)
    if profile is None or not profile.active or profile.role is None:
        return False

    role = profile.role
    if not role.active:
        return False

    return role.rolepermission_set.filter(
        permission__code=permission_code,
        permission__active=True,
        allow=True,
    ).exists()
