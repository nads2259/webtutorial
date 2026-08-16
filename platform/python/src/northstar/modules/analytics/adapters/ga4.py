"""Reference in-memory GA4 adapter behind :class:`Ga4AdapterPort` (docs/17 §9, FR-ANL-006).

This is the OPTIONAL, swappable GA4 seam. It returns a :class:`Ga4Metric`, which is
non-authoritative by construction and always carries source freshness + the mapping that produced
it — so GA4 figures
are never presented as authoritative learner state (EVAL-ANL-006). A real GA4 Data API adapter is a
drop-in replacement behind the same port; disabling / omitting this adapter changes nothing about
first-party authority (GA independence, EVAL-ANL-002).

The adapter deliberately reports its own measurement instant (``as_of``) DISTINCT from the retrieval
instant (``retrieved_at``) and tolerates GA sampling — it never claims freshness it does not have.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..domain.model import Ga4Mapping, Ga4Metric, SourceFreshness


class InMemoryGa4Adapter:
    """Deterministic in-memory GA4 adapter for the reference build and tests.

    ``seed_values`` maps a GA4 event name to a canned aggregate figure. An unseeded mapping returns
    ``0.0`` (GA reports no rows) rather than fabricating a value. ``freshness_lag`` models GA's
    processing delay so ``as_of`` trails ``retrieved_at``.
    """

    def __init__(
        self,
        *,
        seed_values: dict[str, float] | None = None,
        freshness_lag: timedelta = timedelta(hours=24),
    ) -> None:
        self._values = dict(seed_values or {})
        self._freshness_lag = freshness_lag

    def fetch_reach(
        self, *, organization_id: str, mapping: Ga4Mapping, metric_name: str, now: datetime
    ) -> Ga4Metric:
        value = float(self._values.get(mapping.ga4_event, 0.0))
        return Ga4Metric(
            metric_name=metric_name,
            value=value,
            mapping=mapping,
            freshness=SourceFreshness(as_of=now - self._freshness_lag, retrieved_at=now),
        )


__all__ = ["InMemoryGa4Adapter"]
