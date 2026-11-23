"""A0 (HG-078): the whole test run is kept off the network and on the AI mock (hesba_testing/network.py, runner.py)."""

import os
import socket
import urllib.request
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase

from . import network, runner


class RunWideGuardTests(SimpleTestCase):
    def test_the_runner_installed_the_guard_for_this_run(self):
        self.assertTrue(network.installed())
        for attempt in (lambda: urllib.request.urlopen("https://api.elevenlabs.io/", timeout=1),
                        lambda: socket.create_connection(("api.elevenlabs.io", 443), timeout=1),
                        lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(("8.8.8.8", 53)),
                        lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect_ex(("1.1.1.1", 443))):
            with self.assertRaises(network.ExternalNetworkBlocked):
                attempt()

    def test_loopback_still_works(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(server.close)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        for host in ("127.0.0.1", "localhost"):
            with self.subTest(host=host):
                client = socket.create_connection((host, port), timeout=2)
                client.close()
                accepted, _ = server.accept()
                accepted.close()

    def test_what_counts_as_loopback(self):
        for host in ("localhost", "127.0.0.1", "127.8.9.10", "::1", "[::1]", b"127.0.0.1", ""):
            with self.subTest(host=host):
                self.assertTrue(network.is_loopback(host))
        for host in ("api.elevenlabs.io", "8.8.8.8", "10.0.0.5", "192.168.1.1", "::ffff:8.8.8.8", "localhost.evil.com"):
            with self.subTest(host=host):
                self.assertFalse(network.is_loopback(host))

    def test_the_guard_is_not_an_os_error(self):
        # Code that turns network failures (OSError) into its own errors must not hide a blocked attempt.
        self.assertFalse(issubclass(network.ExternalNetworkBlocked, OSError))

    def test_the_run_is_on_the_ai_mock_with_no_key(self):
        self.assertEqual((settings.AI_MODE, settings.ELEVENLABS_API_KEY, settings.ANTHROPIC_API_KEY), ("mock", "", ""))
        self.assertEqual((os.environ.get("AI_MODE"), os.environ.get("ELEVENLABS_API_KEY"), os.environ.get("ANTHROPIC_API_KEY")), ("mock", "", ""))

    def test_spawned_workers_install_the_guard_and_the_ai_env(self):
        # Forked workers inherit the patches; a spawned worker starts clean and runs _pin_worker before Django.
        with mock.patch.object(network, "install") as install, mock.patch.dict(os.environ, {"AI_MODE": "live", "ELEVENLABS_API_KEY": "real", "ANTHROPIC_API_KEY": "real"}):
            runner._pin_worker("")
            self.assertEqual((os.environ["AI_MODE"], os.environ["ELEVENLABS_API_KEY"], os.environ["ANTHROPIC_API_KEY"]), ("mock", "", ""))
        install.assert_called_once_with()
        self.assertIs(runner.PinnedParallelTestSuite.process_setup, runner._pin_worker)

    def test_install_is_idempotent(self):
        connect = socket.socket.connect
        network.install()
        self.assertIs(socket.socket.connect, connect)  # a second install changes nothing
