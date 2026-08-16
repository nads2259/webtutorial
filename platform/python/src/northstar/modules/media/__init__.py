"""Northstar media module (TASK-MEDIA-H04, GATE-MEDIA-GA).

A validated media capability for video/audio/image assets (FR-CNT-009/010, NFR-A11Y-003,
NFR-SEC-004). Two invariants sit at its core:

* **Validated ingestion (EVAL-MED-001, reuses EVAL-SEC-004).** Every asset is ingested ONLY
  through the H02 :class:`~northstar.adapters.upload.ValidatingObjectStorage` /
  :class:`~northstar.adapters.upload.UploadValidator` — there is no unvalidated media write path,
  so a mismatched/malicious asset is refused before any byte reaches storage.
* **Accessibility gate (EVAL-MED-002, NFR-A11Y-003).** Publishing a VIDEO or AUDIO asset REQUIRES
  a transcript AND captions; publishing an IMAGE requires alt text OR an explicit decorative flag.
  A publish attempt without the required alternative is REJECTED with a typed error — a hard
  invariant, never advisory.

Time-based media exposes addressable time-selectors (caption cues / transcript segments by
timecode, FR-CNT-010) usable for annotation and citation. Hexagonal: the ``domain`` layer is pure;
infrastructure (SQLAlchemy repository with forced tenant RLS, the validated storage seam, the
reference caption generator) lives behind ports in ``adapters``.
"""
