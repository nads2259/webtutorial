"""Deterministic scoring adapter implementing :class:`ScoringPort` (FR-SIM-006, EVAL-SIM-002/006).

A thin adapter over the pure domain :func:`compute_score`: the same ``(definition, inputs, seed)``
always yields the same :class:`Score`, so a run is replayable to an identical score. The hidden
scoring key is deliberately NOT an input here — scoring stays reproducible and independent of the
secret the AI coach must never see.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..domain.model import Score, SimulationDefinition, compute_score


class DeterministicScoring:
    """Reference scoring: a pure, replayable function of definition + inputs + seed."""

    __slots__ = ()

    def score(
        self,
        *,
        score_id: str,
        run_id: str,
        organization_id: str,
        definition: SimulationDefinition,
        inputs: Mapping[str, object],
        seed: str,
    ) -> Score:
        value = compute_score(
            definition_hash=definition.content_hash(),
            inputs=inputs,
            seed=seed,
            profile_id=definition.evaluation.profile_id,
            profile_version=definition.evaluation.version,
        )
        return Score(
            score_id=score_id,
            run_id=run_id,
            organization_id=organization_id,
            profile_id=definition.evaluation.profile_id,
            profile_version=definition.evaluation.version,
            seed=seed,
            value=value,
        )
