from django.shortcuts import render


OWNER_ROLE_CODE = "owner"


def _user_can_start_setup(user):
    """Return whether the current user may start the initial setup.

    The project already has a role/profile model, but the full permission gate for
    setup is not yet a dedicated permission. Keep this view scoped to UI behavior:
    superusers and users with Owner role can start; authenticated non-owner roles
    see the no-permission state. Anonymous dev/smoke access can still render the
    approved gate without introducing new auth redirects or schema changes.
    """

    if not getattr(user, "is_authenticated", False):
        return True

    if getattr(user, "is_superuser", False):
        return True

    profile = getattr(user, "hesba_profile", None)
    role = getattr(profile, "role", None)
    return getattr(role, "code", None) == OWNER_ROLE_CODE


def setup_gate(request):
    """Approved Setup Gate UI route for milestone 113A.

    This screen is navigation/UI only. It does not create setup records, update
    dashboard logic, or touch any commercial cycle behavior.
    """

    can_start_setup = _user_can_start_setup(request.user)
    context = {
        "can_start_setup": can_start_setup,
        "start_setup_url": "/setup/activity/",
        "logout_url": "/login/",
    }
    return render(request, "setup/gate.html", context)
