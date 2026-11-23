"""Test runner that keeps a whole run on one Cairo calendar day (see clock.py), off the network (see network.py) and
on the AI mock (A0, HG-078).

Set ``HESBA_TEST_REAL_CLOCK=1`` to run with the unpinned clock.
"""

import os
from datetime import date

from django.test.runner import DiscoverRunner, ParallelTestSuite
from django.test.utils import override_settings

from . import clock, network

#: Whatever the developer's environment says, a test run never holds an AI key and never talks to a live AI provider.
AI_TEST_SETTINGS = {"AI_MODE": "mock", "ELEVENLABS_API_KEY": "", "ANTHROPIC_API_KEY": ""}  # A2 (HG-080): no Anthropic key either


def _ai_env():
    # Read by config() when a spawned worker imports the settings (forked workers inherit the parent's settings).
    os.environ.update(AI_TEST_SETTINGS)


def _pin_worker(anchor_iso):
    # Only runs for the "spawn" start method (macOS/Windows), before the worker imports Django; forked workers
    # inherit the parent's patches.  ``anchor_iso`` is empty when pinning is off.
    network.install()
    _ai_env()
    if anchor_iso and clock.anchor_day() is None:
        clock.install(anchor=date.fromisoformat(anchor_iso))


class PinnedParallelTestSuite(ParallelTestSuite):
    process_setup = _pin_worker

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        anchor = clock.anchor_day()
        self.process_setup_args = (anchor.isoformat() if anchor else "",)


class HesbaTestRunner(DiscoverRunner):
    parallel_test_suite = PinnedParallelTestSuite
    _ai_override = None

    def setup_test_environment(self, **kwargs):
        network.install()
        _ai_env()
        # Before build_suite(), which imports the modules with TODAY constants.
        if not os.environ.get("HESBA_TEST_REAL_CLOCK"):
            clock.install()
        super().setup_test_environment(**kwargs)

    def run_checks(self, databases):
        # The checks see the server's real AI settings (ai.E001 rightly refuses mock with DEBUG off, and the runner
        # turns DEBUG off); the tests then run on the mock with no key. Forked workers start after this.
        super().run_checks(databases)
        if self._ai_override is None:
            self._ai_override = override_settings(**AI_TEST_SETTINGS)
            self._ai_override.enable()

    def teardown_test_environment(self, **kwargs):
        if self._ai_override is not None:
            self._ai_override.disable()
            self._ai_override = None
        super().teardown_test_environment(**kwargs)
        clock.uninstall()
        network.uninstall()
