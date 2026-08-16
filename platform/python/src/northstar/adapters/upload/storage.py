"""A validating object-storage decorator: no unvalidated write path to media storage (EVAL-SEC-004).

:class:`ValidatingObjectStorage` wraps any object store that exposes ``put``/``get``/``exists`` and
runs the deny-by-default :class:`~northstar.adapters.upload.UploadValidator` on the bytes *before*
delegating the write. Because the wrapped store is only reachable through this decorator, every
byte that reaches media storage has passed content-based MIME sniffing, the size + decompression
-bomb caps, the SVG/HTML active-content refusal and the quarantine scan first (docs/08 §5). A
rejected write raises :class:`~northstar.kernel.security.upload.UploadRejected` (audited) and the
inner store never receives the bytes.

The decorator structurally satisfies the knowledge module's ``ObjectStoragePort`` (put/get/exists)
without importing it, keeping this shared adapter decoupled from the module (duck typing, mirroring
the object-storage reference adapters). The ``put`` ``content_type`` is treated as the *declared*
type (never trusted) and the sniffed type is what is persisted.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from northstar.kernel.context import Actor

from .validator import UploadValidator


@runtime_checkable
class ObjectStoreLike(Protocol):
    """The minimal object-store surface the decorator wraps (put/get/exists by key)."""

    def put(self, *, key: str, data: bytes, content_type: str) -> str: ...

    def get(self, *, key: str) -> bytes | None: ...

    def exists(self, *, key: str) -> bool: ...


class ValidatingObjectStorage:
    """Object storage that validates every ``put`` via the upload validator (deny-by-default)."""

    def __init__(
        self,
        *,
        inner: ObjectStoreLike,
        validator: UploadValidator,
    ) -> None:
        self._inner = inner
        self._validator = validator

    def put(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str,
        actor: Actor | None = None,
        correlation_id: str | None = None,
    ) -> str:
        """Validate ``data`` then store it under ``key``; raise ``UploadRejected`` on refusal.

        The persisted content type is the *sniffed* type (not the caller's declared value), so a
        stored object is always labelled by what its bytes actually are.
        """
        validated = self._validator.validate(
            filename=key,
            declared_content_type=content_type,
            data=data,
            actor=actor,
            correlation_id=correlation_id,
        )
        return self._inner.put(key=key, data=data, content_type=validated.sniffed_content_type)

    def get(self, *, key: str) -> bytes | None:
        return self._inner.get(key=key)

    def exists(self, *, key: str) -> bool:
        return self._inner.exists(key=key)


__all__ = ["ObjectStoreLike", "ValidatingObjectStorage"]
