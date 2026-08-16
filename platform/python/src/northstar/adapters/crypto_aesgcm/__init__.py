"""AES-256-GCM reference adapter for the kernel :class:`EncryptionPort` (infra allowed — rule 10).

This package is the *only* place the ``cryptography`` library is imported for at-rest encryption
(rule 50: crypto lives in an adapter, never in the kernel or a domain layer). It provides the
reference envelope-encryption implementation and the KEK-resolution seam that a production KMS/HSM
adapter slots into without any change to callers.
"""

from __future__ import annotations

from .encryptor import (
    MASTER_KEY_ENV,
    AesGcmEncryptor,
    MasterKeyInvalid,
    MasterKeyMissing,
    load_master_key,
)

__all__ = [
    "MASTER_KEY_ENV",
    "AesGcmEncryptor",
    "MasterKeyInvalid",
    "MasterKeyMissing",
    "load_master_key",
]
