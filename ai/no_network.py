"""HG-078 (A0): the base every AI test uses. Any attempt to reach the network fails the test.

The guard raises ``RealNetworkAttempt``, which is not an ``OSError``: the live client turns ``OSError`` and urllib errors
into a ``ProviderError``, and a guard it could swallow would let an error-path test pass while reaching out. Every
attempt is also recorded and checked again at cleanup, so even a caught attempt fails the test.
"""

import socket
import urllib.request
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings


class RealNetworkAttempt(AssertionError):
    """A test tried to open a socket or a URL."""


class NoNetworkMixin:
    def setUp(self):
        super().setUp()
        self.network_attempts = []

        def guard(name):
            def refuse(*args, **kwargs):
                self.network_attempts.append(name)
                raise RealNetworkAttempt(f"An AI test tried to reach the network ({name}).")
            return refuse

        for patcher in (mock.patch.object(urllib.request, "urlopen", guard("urllib.request.urlopen")),
                        mock.patch.object(socket, "create_connection", guard("socket.create_connection")),
                        mock.patch.object(socket.socket, "connect", guard("socket.socket.connect")),
                        mock.patch.object(socket.socket, "connect_ex", guard("socket.socket.connect_ex"))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._no_network_was_attempted)

    def _no_network_was_attempted(self):
        if self.network_attempts:
            raise RealNetworkAttempt(f"An AI test tried to reach the network: {', '.join(self.network_attempts)}.")


#: Mock by default (tests run with DEBUG off, where an empty AI_MODE means off), and no key from the environment.
AI_TEST_SETTINGS = {"AI_MODE": "mock", "ELEVENLABS_API_KEY": "", "ELEVENLABS_VOICE_ID": "hesba-placeholder-voice",
                    "ELEVENLABS_MODEL_ID": "eleven_multilingual_v2", "ANTHROPIC_API_KEY": "", "ANTHROPIC_MODEL": "claude-opus-5-5"}


@override_settings(**AI_TEST_SETTINGS)
class AiSimpleTestCase(NoNetworkMixin, SimpleTestCase):
    pass


@override_settings(**AI_TEST_SETTINGS)
class AiTestCase(NoNetworkMixin, TestCase):
    pass
