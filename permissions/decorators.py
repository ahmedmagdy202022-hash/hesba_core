"""View-level permission gating.

``user_has_permission`` has always been the intended gate, but nothing called
it, so being signed in was the only real check. docs/permissions_map.md is
blunt about why that is not enough: "Menu hiding is not enough. Every route and
every service must check permissions in the backend."

These helpers do the calling and nothing else — the decision itself stays in
``permissions.services`` so there is one answer to what a user may do.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied

from .services import user_has_permission


def require_permission(permission_code):
    """Refuse the view unless the signed-in user holds ``permission_code``.

    Raises PermissionDenied (403) rather than redirecting: the visitor is already
    authenticated, so sending them to a login page would be a lie about what
    went wrong.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not user_has_permission(request.user, permission_code):
                raise PermissionDenied(
                    f"This screen needs the {permission_code} permission."
                )
            return view(request, *args, **kwargs)

        wrapped.required_permission = permission_code
        return wrapped

    return decorator


def require_any_permission(*permission_codes):
    """Refuse the view unless the user holds at least one of the codes.

    For screens that assemble themselves from several sources — a dashboard is
    worth showing to anyone who can see any part of it, and each part gates
    itself separately.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not any(user_has_permission(request.user, code) for code in permission_codes):
                raise PermissionDenied(
                    "This screen needs one of: " + ", ".join(permission_codes)
                )
            return view(request, *args, **kwargs)

        wrapped.required_permissions = permission_codes
        return wrapped

    return decorator


def permitted_codes(user, permission_codes):
    """The subset of ``permission_codes`` this user holds.

    Screens use this to decide which cards to build, so a single pass over the
    descriptors answers what the user may see.
    """

    return frozenset(code for code in permission_codes if user_has_permission(user, code))
