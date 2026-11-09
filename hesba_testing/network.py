"""A0 (HG-078): no test in the run reaches a machine other than this one.

``install()`` patches ``socket.create_connection``, ``socket.socket.connect`` / ``connect_ex`` and
``urllib.request.urlopen`` so that any address other than loopback (``localhost``, ``127.0.0.0/8``, ``::1``) raises
``ExternalNetworkBlocked``. Unix-domain sockets and loopback pass through untouched. The exception is not an
``OSError``, so code that turns network errors into its own failures cannot swallow it.

PostgreSQL is unaffected: psycopg talks through libpq, which does not use Python sockets.

The test runner installs it for the whole run, and again in each spawned parallel worker (``runner._pin_worker``).
The AI tests add a stricter guard of their own (``ai/no_network.py``) that refuses loopback too.
"""

import ipaddress
import socket
import urllib.parse
import urllib.request
from unittest import mock

LOOPBACK_NAMES = frozenset({"", "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"})

_patchers = []


class ExternalNetworkBlocked(AssertionError):
    """A test tried to reach a machine other than this one."""


def is_loopback(host):
    if isinstance(host, (bytes, bytearray)):
        host = bytes(host).decode("ascii", "ignore")
    host = str(host or "").strip().strip("[]").lower()
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False  # any other name could resolve anywhere


def _refuse(what, host):
    raise ExternalNetworkBlocked(f"A test tried to reach {host!r} ({what}); tests must not use the network.")


def install():
    if _patchers:
        return
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_create_connection, real_urlopen = socket.create_connection, urllib.request.urlopen

    def checked(sock, address, what):
        if sock.family in (socket.AF_INET, socket.AF_INET6) and isinstance(address, tuple) and address and not is_loopback(address[0]):
            _refuse(what, address[0])

    def connect(sock, address):
        checked(sock, address, "socket.connect")
        return real_connect(sock, address)

    def connect_ex(sock, address):
        checked(sock, address, "socket.connect_ex")
        return real_connect_ex(sock, address)

    def create_connection(address, *args, **kwargs):
        if isinstance(address, tuple) and address and not is_loopback(address[0]):
            _refuse("socket.create_connection", address[0])
        return real_create_connection(address, *args, **kwargs)

    def urlopen(url, *args, **kwargs):
        full_url = url.full_url if isinstance(url, urllib.request.Request) else str(url)
        parsed = urllib.parse.urlsplit(full_url)
        if parsed.scheme in ("http", "https", "ftp") and not is_loopback(parsed.hostname):
            _refuse("urllib.request.urlopen", parsed.hostname)
        return real_urlopen(url, *args, **kwargs)

    for patcher in (mock.patch.object(socket.socket, "connect", connect), mock.patch.object(socket.socket, "connect_ex", connect_ex),
                    mock.patch.object(socket, "create_connection", create_connection),
                    mock.patch.object(urllib.request, "urlopen", urlopen)):
        patcher.start()
        _patchers.append(patcher)


def uninstall():
    while _patchers:
        _patchers.pop().stop()


def installed():
    return bool(_patchers)
