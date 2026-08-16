"""AES-256-GCM implementation of :class:`~northstar.kernel.security.ports.EncryptionPort`.

Envelope encryption (spec ``security/cryptography-and-key-management.md`` §2): a 256-bit **Key
Encryption Key (KEK)** protects data at rest. This reference adapter uses the KEK directly as the
GCM data key; the ``version`` byte in every token reserves the seam for a future envelope scheme
that stores a wrapped per-record Data Encryption Key (DEK) alongside the ciphertext without
breaking old tokens.

Token layout (all lengths fixed except the trailing ciphertext)::

    version (1 byte = 0x01) || nonce (12 bytes / 96 bits) || ciphertext + GCM tag (16 bytes)

- **Nonce**: a fresh 96-bit CSPRNG value per :meth:`encrypt` (never a counter, never reused). This
  is the GCM-recommended nonce size; a fresh random nonce per operation keeps reuse probability
  negligible for the volumes involved here.
- **Integrity/constant-time**: GCM authenticates ciphertext + AAD; the ``cryptography`` library
  verifies the tag in constant time and raises :class:`InvalidTag` on any mismatch, which is mapped
  to the uniform kernel :class:`DecryptionError` (no plaintext, no oracle).

## KMS swap seam (rule 50, spec §3)

Production KEKs live in a FIPS-validated KMS/HSM, not in an environment variable. Two supported
migrations, neither of which changes any caller:

1. *Externally-provided key*: resolve the 32-byte key from the secret manager / KMS "get secret"
   API and hand it to :class:`AesGcmEncryptor` — replace only :func:`load_master_key`.
2. *KMS-native crypto*: implement :class:`~northstar.kernel.security.ports.EncryptionPort` with an
   adapter that calls the KMS ``Encrypt``/``Decrypt`` (or ``GenerateDataKey`` for true envelope
   encryption) APIs and emits a token whose ``version`` byte distinguishes it from ``0x01``.

Dev/test deployments (``NORTHSTAR_MASTER_KEY`` unset) fall back to a deterministic, clearly-insecure
derived key so the one-touch dev experience works offline; **production requires the key to be set**
(:func:`load_master_key` raises :class:`MasterKeyMissing` when it is absent outside dev/test).
"""

from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from northstar.kernel.security.ports import DecryptionError

MASTER_KEY_ENV = "NORTHSTAR_MASTER_KEY"
KEY_BYTES = 32  # AES-256
NONCE_BYTES = 12  # 96-bit GCM nonce
_TOKEN_VERSION = 0x01
_DEV_ENVIRONMENTS = frozenset({"dev", "development", "test", "testing", "local"})
_ENV_NAME_ENV = "NORTHSTAR_ENV"


class MasterKeyMissing(RuntimeError):  # noqa: N818 canonical error name
    """The master KEK was required (non-dev environment) but not configured."""

    def __init__(self, env_var: str = MASTER_KEY_ENV) -> None:
        super().__init__(
            f"required master key '{env_var}' is not set; production deployments MUST provide a "
            "base64-encoded 32-byte key from the secret manager/KMS"
        )
        self.env_var = env_var


class MasterKeyInvalid(RuntimeError):  # noqa: N818 canonical error name
    """The configured master KEK was present but not a valid base64-encoded 32-byte key."""


class AesGcmEncryptor:
    """AES-256-GCM authenticated encryption at rest (implements ``EncryptionPort``).

    ``key`` MUST be exactly 32 bytes (AES-256). The instance is immutable and safe to share; each
    :meth:`encrypt` draws its own random nonce so it is concurrency-safe under a single key.
    """

    __slots__ = ("_aesgcm",)

    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise MasterKeyInvalid(f"AES-256 key must be exactly {KEY_BYTES} bytes, got {len(key)}")
        # AESGCM validates the key length again and holds it; the raw key is never stored on self.
        self._aesgcm = AESGCM(key)

    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = self._aesgcm.encrypt(nonce, plaintext, aad)
        return bytes([_TOKEN_VERSION]) + nonce + ciphertext

    def decrypt(self, token: bytes, aad: bytes) -> bytes:
        # A valid token is version(1) + nonce(12) + ciphertext(>=16 for the GCM tag).
        if len(token) < 1 + NONCE_BYTES + 16:
            raise DecryptionError("ciphertext token is truncated or malformed")
        if token[0] != _TOKEN_VERSION:
            raise DecryptionError(f"unsupported ciphertext token version: {token[0]}")
        nonce = token[1 : 1 + NONCE_BYTES]
        ciphertext = token[1 + NONCE_BYTES :]
        try:
            return self._aesgcm.decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            # Uniform failure: wrong key, tampered token and AAD mismatch are indistinguishable.
            raise DecryptionError("authenticated decryption failed") from exc


def _derive_dev_key(seed: str) -> bytes:
    """Return a deterministic, INSECURE 32-byte key for offline dev/test only (never production)."""
    return hashlib.sha256(f"northstar-insecure-dev-kek::{seed}".encode()).digest()


def load_master_key(
    env: Mapping[str, str] | None = None,
    *,
    environment: str | None = None,
) -> bytes:
    """Resolve the 32-byte AES KEK from configuration (deny-by-default outside dev/test).

    Reads ``NORTHSTAR_MASTER_KEY`` as a base64-encoded 32-byte key. When it is absent the behavior
    depends on the environment (``NORTHSTAR_ENV``, default ``development``): dev/test derive a
    deterministic insecure key so the platform runs offline, while any other environment raises
    :class:`MasterKeyMissing` — a production process must be handed a real key. This is the single
    place to replace when moving KEK custody to a KMS/secret manager (see module docstring).
    """
    source = os.environ if env is None else env
    raw = source.get(MASTER_KEY_ENV)
    if raw:
        try:
            key = base64.b64decode(raw, validate=True)
        except (ValueError, base64.binascii.Error) as exc:  # type: ignore[attr-defined]
            raise MasterKeyInvalid(f"'{MASTER_KEY_ENV}' is not valid base64") from exc
        if len(key) != KEY_BYTES:
            raise MasterKeyInvalid(
                f"'{MASTER_KEY_ENV}' must decode to {KEY_BYTES} bytes, got {len(key)}"
            )
        return key

    env_name = (environment or source.get(_ENV_NAME_ENV) or "development").lower()
    if env_name in _DEV_ENVIRONMENTS:
        return _derive_dev_key(env_name)
    raise MasterKeyMissing()
