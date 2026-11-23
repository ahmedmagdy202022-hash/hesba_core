"""Every shipped JavaScript file must at least parse.

The Django suite never executes the browser scripts, so a bad merge could
leave one unparsable without a single test failing. ``node --check`` catches
that; the test is skipped where Node is not installed.
"""

import shutil
import subprocess
from pathlib import Path
from unittest import SkipTest

from django.conf import settings
from django.test import SimpleTestCase


class StaticJavaScriptTests(SimpleTestCase):
    def test_every_script_parses(self):
        node = shutil.which("node")
        if node is None:
            raise SkipTest("node is not installed")
        scripts = sorted(Path(settings.BASE_DIR, "static").rglob("*.js"))
        self.assertTrue(scripts)
        for script in scripts:
            with self.subTest(script=str(script.relative_to(settings.BASE_DIR))):
                result = subprocess.run([node, "--check", str(script)], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
