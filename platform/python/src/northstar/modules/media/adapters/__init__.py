"""Media infrastructure adapters (behind application ports, rule 10/LAW-12).

* :class:`~northstar.modules.media.adapters.tables.MediaTables` + the SQLAlchemy repository
  (forced tenant RLS) own the ``northstar_media`` schema;
* :class:`~northstar.modules.media.adapters.storage.ValidatingMediaStorage` is the ONLY media write
  seam, delegating to the shared H02 ``ValidatingObjectStorage`` (no unvalidated write path);
* :class:`~northstar.modules.media.adapters.caption.ReferenceCaptionGenerator` is the reference
  transcription/captioning seam (a real ASR engine is an adapter swap).
"""

from __future__ import annotations

from .caption import ReferenceCaptionGenerator
from .repositories import InMemoryMediaRepository, SqlAlchemyMediaRepository
from .storage import ValidatingMediaStorage, build_media_storage
from .tables import MEDIA_SCHEMA, MediaTables, build_media_tables

__all__ = [
    "MEDIA_SCHEMA",
    "InMemoryMediaRepository",
    "MediaTables",
    "ReferenceCaptionGenerator",
    "SqlAlchemyMediaRepository",
    "ValidatingMediaStorage",
    "build_media_storage",
    "build_media_tables",
]
