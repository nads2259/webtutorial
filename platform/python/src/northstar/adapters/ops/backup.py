"""Encrypted backup + point-in-time-restore codec (EVAL-DATA-004, NFR-OPS-003).

The DR runbook (docs/18 §14) requires rehearsed, verifiable backup/restore with integrity checks.
This codec turns a relational snapshot (an ordered list of row dicts per table) into an opaque,
ENCRYPTED backup blob by serialising it deterministically and sealing it through the kernel
:class:`~northstar.kernel.security.ports.EncryptionPort` (AES-256-GCM at rest, rule 50). Restore
authenticates + decrypts the blob and returns the exact snapshot; a wrong key, tampered blob or
mismatched AAD fails closed with a :class:`DecryptionError` (never partial/plaintext). A SHA-256
content hash over the canonical plaintext is bound as AAD and recorded in the manifest so a
restore can prove the recovered state matches what was captured. The codec is storage-agnostic:
the caller supplies rows read from any datastore (e.g. the ephemeral PostgreSQL drill).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from northstar.kernel.security.ports import EncryptionPort

Snapshot = Mapping[str, Sequence[Mapping[str, Any]]]

_AAD_PREFIX = b"northstar.backup.v1:"


def _canonical_bytes(snapshot: Snapshot) -> bytes:
    """Serialise a snapshot to canonical, sorted JSON bytes (stable hash + deterministic blob)."""
    normalised = {table: [dict(row) for row in rows] for table, rows in snapshot.items()}
    return json.dumps(normalised, sort_keys=True, separators=(",", ":"), default=str).encode()


def content_hash(snapshot: Snapshot) -> str:
    """Return the SHA-256 hex digest of the canonical snapshot bytes."""
    return hashlib.sha256(_canonical_bytes(snapshot)).hexdigest()


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Integrity metadata for one encrypted backup (recorded alongside the blob as evidence)."""

    label: str
    tables: tuple[str, ...]
    row_count: int
    content_hash: str
    ciphertext_bytes: int


@dataclass(frozen=True, slots=True)
class Backup:
    """An encrypted backup blob plus its integrity manifest."""

    blob: bytes
    manifest: BackupManifest


class EncryptedBackupCodec:
    """Encode/decode encrypted, integrity-checked relational snapshots via the EncryptionPort."""

    def __init__(self, *, encryptor: EncryptionPort) -> None:
        self._encryptor = encryptor

    def _aad(self, digest: str) -> bytes:
        return _AAD_PREFIX + digest.encode()

    def encode(self, snapshot: Snapshot, *, label: str) -> Backup:
        """Seal ``snapshot`` into an encrypted backup blob bound to its content hash (AAD)."""
        plaintext = _canonical_bytes(snapshot)
        digest = hashlib.sha256(plaintext).hexdigest()
        blob = self._encryptor.encrypt(plaintext, self._aad(digest))
        manifest = BackupManifest(
            label=label,
            tables=tuple(sorted(snapshot.keys())),
            row_count=sum(len(rows) for rows in snapshot.values()),
            content_hash=digest,
            ciphertext_bytes=len(blob),
        )
        return Backup(blob=blob, manifest=manifest)

    def decode(self, backup: Backup) -> dict[str, list[dict[str, Any]]]:
        """Authenticate + decrypt a backup and return its snapshot; fails closed on any tampering.

        Re-hashes the recovered plaintext and checks it against the manifest, so a restore proves
        integrity end-to-end (the AEAD tag already binds ciphertext + AAD; defence in depth).
        """
        plaintext = self._encryptor.decrypt(backup.blob, self._aad(backup.manifest.content_hash))
        digest = hashlib.sha256(plaintext).hexdigest()
        if digest != backup.manifest.content_hash:
            raise ValueError("restored snapshot hash does not match the backup manifest")
        decoded: dict[str, list[dict[str, Any]]] = json.loads(plaintext)
        return decoded
