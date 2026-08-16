"""Authenticated-encryption-at-rest port (pure kernel abstraction — rule 10/50, LAW-08/12).

:class:`EncryptionPort` is the single authoritative seam through which any module protects a
sensitive value at rest. It is deliberately tiny (ISP, rule 20) and infrastructure-free: it speaks
only ``bytes`` and imports nothing beyond the stdlib and the kernel error base, so the domain and
application layers can depend on it without ever importing a crypto library. Adapters (the
AES-256-GCM reference adapter, a future KMS/HSM adapter) implement it behind the boundary.

Both operations take *associated data* (AAD): additional authenticated — but not encrypted — bytes
that bind the ciphertext to its context (e.g. the owning subject + credential id). Decryption of a
token presented with different AAD fails, so a ciphertext cannot be silently relocated to a
different record (tamper/duplication resistance, per the cryptography-and-key-management spec §2).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..errors import KernelError


class DecryptionError(KernelError):
    """Authenticated decryption failed: wrong key, tampered token, or AAD mismatch.

    Raised by any :class:`EncryptionPort` adapter when the authentication tag does not verify or
    the token is malformed. The failure is deliberately uniform (it never distinguishes *why*
    verification failed) so it cannot be used as an oracle. The adapter maps the provider's own
    integrity exception onto this typed kernel error; the plaintext is never returned on failure.
    """


@runtime_checkable
class EncryptionPort(Protocol):
    """Authenticated symmetric encryption of a value at rest (AEAD).

    Implementations MUST provide confidentiality **and** integrity (an AEAD construction such as
    AES-256-GCM): a modification to any byte of the returned token, or a change to ``aad``, MUST
    cause :meth:`decrypt` to raise :class:`DecryptionError` rather than return altered plaintext.
    Each :meth:`encrypt` call MUST use a fresh, unique nonce so encrypting the same plaintext twice
    yields distinct tokens (nonce reuse under one key is catastrophic for GCM).
    """

    def encrypt(self, plaintext: bytes, aad: bytes) -> bytes:
        """Return an opaque, self-describing ciphertext token binding ``plaintext`` to ``aad``.

        The token carries everything :meth:`decrypt` needs except the key (a version marker, the
        nonce and the ciphertext+tag); callers treat it as opaque bytes and persist it verbatim.
        """
        ...

    def decrypt(self, token: bytes, aad: bytes) -> bytes:
        """Return the plaintext for ``token`` iff its tag verifies under the same ``aad``.

        Raises :class:`DecryptionError` on a wrong key, a tampered/truncated token or an ``aad``
        that does not match the one supplied at encryption time. Never returns partial plaintext.
        """
        ...
