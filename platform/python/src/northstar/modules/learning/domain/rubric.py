"""Deterministic pedagogical rubric for the AI tutor (EVAL-AI-009 automatable slice).

EVAL-AI-009 is defined as *rubric-based human evaluation*. This module implements the
DETERMINISTIC, automatable slice of that rubric so the tutor's structural pedagogy (clarity,
scaffolding, misconception handling, next-step) is machine-checkable on every answer, and it flags
HONESTLY that the subjective quality judgement remains human-graded (``human_review_required``).

Pure and infrastructure-free (rule 10). The scorer never sees an assessment answer key; it only
inspects the already-guarded tutor answer text, so it cannot itself leak one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Signal vocabularies (lowercased) for each automatable dimension. Deterministic by construction.
_SCAFFOLD = ("first", "then", "next", "step", "start by", "begin", "because", "so that", "1.", "2.")
_MISCONCEPTION = (
    "common mistake",
    "misconception",
    "a common error",
    "instead of",
    "rather than",
    "avoid",
    "note that",
    "be careful",
    "not ",
)
_NEXT_STEP = ("next", "try", "practice", "review", "then you", "you can now", "continue", "explore")
_SENTENCE = re.compile(r"[.!?]")


@dataclass(frozen=True, slots=True)
class RubricResult:
    """The deterministic pedagogical-rubric result for one tutor answer (EVAL-AI-009 slice)."""

    clarity: float
    scaffolding: float
    misconception_handling: float
    next_step: float
    passed: bool
    human_review_required: bool = True

    @property
    def overall(self) -> float:
        return round(
            (self.clarity + self.scaffolding + self.misconception_handling + self.next_step) / 4, 4
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "clarity": self.clarity,
            "scaffolding": self.scaffolding,
            "misconception_handling": self.misconception_handling,
            "next_step": self.next_step,
            "overall": self.overall,
            "passed": self.passed,
            "human_review_required": self.human_review_required,
        }


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


def score_pedagogy(answer: str, *, grounded: bool, refused: bool) -> RubricResult:
    """Deterministically score the automatable pedagogy dimensions of a tutor answer.

    A refused answer scores zero on every dimension and does not pass (a safe refusal is correct
    behaviour but is not a pedagogical answer). ``grounded`` (the answer carries >=1 valid citation)
    strengthens the clarity signal, tying pedagogy to grounding (EVAL-AI-009/011).
    """
    if refused or not answer.strip():
        return RubricResult(0.0, 0.0, 0.0, 0.0, passed=False)
    lowered = answer.lower()
    sentences = [s for s in _SENTENCE.split(answer) if s.strip()]

    clarity = 1.0 if (grounded and len(sentences) >= 1 and len(answer.split()) >= 4) else 0.0
    scaffolding = 1.0 if _contains_any(lowered, _SCAFFOLD) else 0.0
    misconception = 1.0 if _contains_any(lowered, _MISCONCEPTION) else 0.0
    next_step = 1.0 if _contains_any(lowered, _NEXT_STEP) else 0.0
    # The automatable pass requires clarity + at least two of the three teaching moves.
    teaching_moves = scaffolding + misconception + next_step
    passed = clarity >= 1.0 and teaching_moves >= 2.0
    return RubricResult(
        clarity=clarity,
        scaffolding=scaffolding,
        misconception_handling=misconception,
        next_step=next_step,
        passed=passed,
    )


__all__ = ["RubricResult", "score_pedagogy"]
