"""ENT-002: the entity a signed-in user is working in right now.

Chosen from the entity bar and kept in the session; empty means the whole
group. The menu, the activity words, the dashboard's activity panel and the
accounting screens follow it.

HG-034: it is also the data scope. While an entity is chosen, documents,
lists, reports, the dashboard and new documents are that entity's only
(entities/scope.py). A user who belongs to some entities only (an
EntityMembership) can never leave them; owners always may.
"""

from contextlib import contextmanager
from contextvars import ContextVar

SESSION_KEY = "hesba_entity"
_current = ContextVar("hesba_current_entity", default=None)


def allowed_entities(user):
    """Entities this user can switch to: their memberships if they have any,
    otherwise every active entity."""

    from .models import Entity

    active = Entity.objects.filter(active=True).order_by("-is_main", "code")
    if getattr(user, "is_authenticated", False) and not is_group_wide(user):
        member_of = list(active.filter(memberships__user=user))
        if member_of:
            return member_of
    return list(active)


def is_group_wide(user):
    """Owners (and superusers) always see the whole group, memberships or not."""

    if getattr(user, "is_superuser", False):
        return True
    profile = getattr(user, "hesba_profile", None)
    role = getattr(profile, "role", None) if profile is not None else None
    return getattr(role, "code", "") == "owner"


def resolve(request):
    from .services import is_multi_entity

    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False) or not is_multi_entity():
        return None
    allowed = allowed_entities(user)
    chosen = request.session.get(SESSION_KEY)
    restricted = len(allowed) < len(allowed_entities(None))
    picked = next((e for e in allowed if e.pk == chosen), None) if chosen is not None else None
    if picked is not None:
        return picked
    # Someone who belongs only to some entities never sees the whole group.
    return _default_for(user, allowed) if restricted else None


def _default_for(user, allowed):
    from .models import EntityMembership

    default = EntityMembership.objects.filter(user=user, is_default=True, entity__in=allowed).select_related("entity").first()
    return default.entity if default else allowed[0]


@contextmanager
def working_in(entity):
    """Read as if ``entity`` were the one being worked in (None: the whole group)."""

    token = _current.set(entity)
    try:
        yield
    finally:
        _current.reset(token)


def current_entity():
    return _current.get()


def effective_activity():
    """(activity, sub_activity) of the current entity, else the installation's."""

    from .services import activity_of

    return activity_of(current_entity())


class CurrentEntityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = _current.set(resolve(request))
        try:
            return self.get_response(request)
        finally:
            _current.reset(token)
