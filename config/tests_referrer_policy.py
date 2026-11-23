"""Customer pages (share link, booking, pay link) must never send ``Referrer-Policy: no-referrer``.

Under that policy browsers send ``Origin: null`` on the page's own form POST, and Django's CSRF check refuses it
(403), so the customer could not approve a quote, book or pay. Django's test client sends no Origin header, so
only a real browser showed it (the خدمي «جرّب» run, 9 Nov 2026). ``same-origin`` still keeps the token in the
URL away from every other site.
"""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


SKIP = {"node_modules", "staticfiles", "env", "lib", "Lib"}


class NoReferrerPolicyTests(SimpleTestCase):
    def test_no_page_or_view_asks_for_no_referrer(self):
        root = Path(settings.BASE_DIR)
        offenders = []
        for pattern in ("**/*.py", "**/*.html"):
            for path in root.glob(pattern):
                parts = path.relative_to(root).parts
                # The app's own code only: no hidden folders (.venv, .git), virtual environments or third-party packages,
                # where Django itself spells the policy out (Codex on #325).
                if (any(part.startswith(".") or part in SKIP or "venv" in part or part == "site-packages" for part in parts[:-1])
                        or path.name.startswith("tests")):
                    continue
                if "no-referrer" in path.read_text(encoding="utf-8", errors="ignore"):
                    offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])
