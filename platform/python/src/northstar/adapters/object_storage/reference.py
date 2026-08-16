"""In-memory and filesystem object-storage reference adapters (FR-CNT-009).

Both structurally satisfy ``northstar.modules.knowledge.application.ports.ObjectStoragePort``
(put/get/exists) without importing it (duck typing keeps this shared adapter decoupled from the
knowledge module). Keys are opaque, caller-chosen, tenant-prefixed strings; the filesystem adapter
maps a key to a path safely under its root and refuses path traversal outside the root.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


class InMemoryObjectStorage:
    """A dict-backed object store for fast, deterministic tests (no filesystem)."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._content_types: dict[str, str] = {}

    def put(self, *, key: str, data: bytes, content_type: str) -> str:
        self._objects[key] = bytes(data)
        self._content_types[key] = content_type
        return key

    def get(self, *, key: str) -> bytes | None:
        stored = self._objects.get(key)
        return None if stored is None else bytes(stored)

    def exists(self, *, key: str) -> bool:
        return key in self._objects

    def content_type(self, *, key: str) -> str | None:
        return self._content_types.get(key)


class FilesystemObjectStorage:
    """A filesystem-backed object store rooted at a directory (local/dev media, S3 seam).

    A key is hashed into a stable relative path under ``root`` so arbitrary key strings (including
    slashes) can never escape the storage root (defense against path traversal, rule 50/08).
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self._root / digest[:2] / digest

    def put(self, *, key: str, data: bytes, content_type: str) -> str:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, *, key: str) -> bytes | None:
        path = self._path_for(key)
        if not path.is_file():
            return None
        return path.read_bytes()

    def exists(self, *, key: str) -> bool:
        return self._path_for(key).is_file()
