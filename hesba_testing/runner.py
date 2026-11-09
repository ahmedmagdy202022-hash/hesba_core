"""Test runner that keeps a whole run on one Cairo calendar day (see clock.py).

Set ``HESBA_TEST_REAL_CLOCK=1`` to run with the unpinned clock.
"""

import os
from datetime import date

from django.test.runner import DiscoverRunner, ParallelTestSuite

from . import clock


def _pin_worker(anchor_iso):
    # Only matters for the "spawn" start method (macOS/Windows); forked workers
    # inherit the parent's patch.  ``anchor_iso`` is empty when pinning is off.
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

    def setup_test_environment(self, **kwargs):
        # Before build_suite(), which imports the modules with TODAY constants.
        if not os.environ.get("HESBA_TEST_REAL_CLOCK"):
            clock.install()
        super().setup_test_environment(**kwargs)

    def teardown_test_environment(self, **kwargs):
        super().teardown_test_environment(**kwargs)
        clock.uninstall()
