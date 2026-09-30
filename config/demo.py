"""DEMO-001: what templates need to know about a showcase install."""

from django.conf import settings


def demo_context(request):
    if not getattr(settings, "DEMO_MODE", False):
        return {}
    return {"demo_mode": True, "demo_password": settings.DEMO_PASSWORD}
