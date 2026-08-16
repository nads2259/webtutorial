"""Object-storage reference adapters (the S3 seam, FR-CNT-009).

Implements the structural ``ObjectStoragePort`` (put/get/exists) with two dependency-light
reference adapters — an in-memory store for tests and a filesystem-backed store for local/dev —
so media can be stored behind a port today and swapped for an S3/GCS adapter later without any
domain change. Stored bytes are treated as opaque and never interpreted.
"""

from __future__ import annotations

from .reference import FilesystemObjectStorage, InMemoryObjectStorage

__all__ = ["FilesystemObjectStorage", "InMemoryObjectStorage"]
