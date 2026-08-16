"""Ports (abstractions) for the analytics application layer (rule 10/20, DIP).

Two seams keep the capabilities infrastructure-free and hold no ambient authority (rule 50):

* :class:`AnalyticsRepositoryPort` — the module's OWN tenant-scoped persistence for the event
  catalog, first-party events and identity stitches (LAW-13). First-party events are the
  authoritative source; nothing here depends on an external analytics provider (FR-ANL-001/002).
* :class:`Ga4AdapterPort` — an OPTIONAL provider-neutral seam for importing GA4 aggregate figures
  (docs/17 §9). It is behind a port so GA4 is a swappable adapter; the reference build ships an
  in-memory implementation. Disabling / omitting it changes NOTHING about first-party authority
  (GA independence, FR-ANL-002/006). Every imported value is labelled non-authoritative and carries
  source freshness + mapping, so GA4 is never returned as authoritative learner state.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from ..domain.model import (
    AnalyticsEvent,
    AnalyticsEventDefinition,
    Ga4Mapping,
    Ga4Metric,
    IdentityStitch,
)


@runtime_checkable
class AnalyticsRepositoryPort(Protocol):
    """Persists/reads the analytics catalog, first-party events and stitches (rule 50, LAW-13)."""

    # Event catalog ------------------------------------------------------
    def add_definition(self, *, organization_id: str, definition: AnalyticsEventDefinition) -> None:
        """Register a NEW event definition; reject an already-registered ``(event_name, version)``
        with :class:`DefinitionAlreadyRegistered` (catalog immutability, FR-ANL-003)."""
        ...

    def get_definition(
        self, *, organization_id: str, event_name: str
    ) -> AnalyticsEventDefinition | None: ...

    def list_definitions(self, *, organization_id: str) -> Sequence[AnalyticsEventDefinition]: ...

    # First-party events (authoritative) --------------------------------
    def record_event(self, *, organization_id: str, event: AnalyticsEvent) -> None: ...

    def list_events(self, *, organization_id: str, event_name: str) -> Sequence[AnalyticsEvent]: ...

    # Identity stitching -------------------------------------------------
    def add_stitch(self, *, organization_id: str, stitch: IdentityStitch) -> None:
        """Persist an identity stitch; idempotent on ``(anonymous_id, user_id)`` (FR-ANL-004)."""
        ...

    def list_stitches(self, *, organization_id: str) -> Sequence[IdentityStitch]: ...


@runtime_checkable
class Ga4AdapterPort(Protocol):
    """OPTIONAL GA4 import seam (docs/17 §9): returns non-authoritative aggregate figures.

    Implementations MUST return a :class:`Ga4Metric` (which is non-authoritative by construction and
    carries source freshness + mapping). A real GA4 Data API adapter is a drop-in swap behind this
    same port; the reference build uses an in-memory adapter.
    """

    def fetch_reach(
        self, *, organization_id: str, mapping: Ga4Mapping, metric_name: str, now: datetime
    ) -> Ga4Metric: ...


__all__ = [
    "AnalyticsRepositoryPort",
    "Ga4AdapterPort",
]
