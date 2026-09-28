"""BACKUP-002: backups only the client can open.

The server keeps the client's *public* key and encrypts every backup to it;
the *private* key is shown to the owner once, when it is created, and is never
stored. Whoever runs the server (Ahmed included) can make backups but cannot
read them; the owner, with the private key, can restore them anywhere.

File format (``.hesba-backup``)::

    b"HESBA-BK1\\n"                 magic + version
    8 bytes   key fingerprint        which public key the file was made for
    32 bytes  ephemeral public key   X25519
    12 bytes  nonce                  AES-GCM
    rest      ciphertext + tag       AES-256-GCM of the gzipped fixture

The AES key is HKDF-SHA256 over the X25519 shared secret, bound to both public
keys; the header is authenticated as associated data, so any change to the
file fails to decrypt instead of restoring something altered.
"""

import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


MAGIC = b"HESBA-BK1\n"
PUBLIC_PREFIX = "hesba-pub-1:"
PRIVATE_PREFIX = "hesba-key-1:"
SETTING_KEY = "backup.public_key"
SUFFIX = ".hesba-backup"
_INFO = b"hesba-backup-v1"


class BackupKeyError(ValueError):
    """A key or an encrypted file that cannot be used."""


def _raw(key):
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text):
    text = "".join(text.split())
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_key_pair():
    """(public_text, private_text) — the private text is for the owner only."""

    private = X25519PrivateKey.generate()
    private_raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    return PUBLIC_PREFIX + _b64(_raw(private.public_key())), PRIVATE_PREFIX + _b64(private_raw)


def load_public(text):
    text = (text or "").strip()
    if not text.startswith(PUBLIC_PREFIX):
        raise BackupKeyError("not a Hesba backup public key")
    try:
        raw = _unb64(text[len(PUBLIC_PREFIX):])
        return X25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise BackupKeyError("damaged public key") from exc


def load_private(text):
    text = (text or "").strip()
    if not text.startswith(PRIVATE_PREFIX):
        raise BackupKeyError("not a Hesba backup private key")
    try:
        raw = _unb64(text[len(PRIVATE_PREFIX):])
        return X25519PrivateKey.from_private_bytes(raw)
    except ValueError as exc:
        raise BackupKeyError("damaged private key") from exc


def fingerprint(public_key):
    return hashlib.sha256(_raw(public_key)).digest()[:8]


def fingerprint_text(public_key):
    return fingerprint(public_key).hex(":").upper()


def _aes_key(shared, ephemeral_raw, recipient_raw):
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=ephemeral_raw + recipient_raw, info=_INFO).derive(shared)


def encrypt(data, public_key):
    ephemeral = X25519PrivateKey.generate()
    ephemeral_raw, recipient_raw = _raw(ephemeral.public_key()), _raw(public_key)
    key = _aes_key(ephemeral.exchange(public_key), ephemeral_raw, recipient_raw)
    nonce = os.urandom(12)
    header = MAGIC + fingerprint(public_key) + ephemeral_raw + nonce
    return header + AESGCM(key).encrypt(nonce, data, header)


def header_fingerprint(blob):
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 8 + 32 + 12 + 16:
        raise BackupKeyError("not a Hesba encrypted backup")
    return blob[len(MAGIC):len(MAGIC) + 8]


def decrypt(blob, private_key):
    wanted = header_fingerprint(blob)
    public_key = private_key.public_key()
    if wanted != fingerprint(public_key):
        raise BackupKeyError("this backup was made for a different key")
    start = len(MAGIC) + 8
    ephemeral_raw, nonce = blob[start:start + 32], blob[start + 32:start + 44]
    header = blob[:start + 44]
    shared = private_key.exchange(X25519PublicKey.from_public_bytes(ephemeral_raw))
    key = _aes_key(shared, ephemeral_raw, _raw(public_key))
    try:
        return AESGCM(key).decrypt(nonce, blob[start + 44:], header)
    except InvalidTag as exc:
        raise BackupKeyError("the backup was changed or damaged") from exc


def configured_public_key():
    """The client's backup public key, or None when backups are not encrypted yet."""

    from .models import SystemSetting

    text = SystemSetting.objects.filter(key=SETTING_KEY, active=True).values_list("value", flat=True).first()
    return load_public(text) if text else None
