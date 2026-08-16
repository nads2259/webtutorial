"""Northstar annotation module (IMPL-010).

Selector-based annotations, comments and notes over versioned knowledge content
(FR-ANN-001..006). An annotation anchors a stable block id + source revision PLUS redundant
text/quote/position/code-range/media selectors (LAW-06, W3C-inspired). Every annotation RETAINS
its ORIGINAL revision target forever (FR-ANN-003); a separate current-target mapping is tracked
when a later revision is remapped. Revision remapping (:mod:`.domain.remap`) is a pure,
DETERMINISTIC ordered-evidence strategy that routes ambiguous/orphaned targets to a review state
rather than silently moving them (FR-ANN-004). Visibility (private/team/workspace/public/editorial/
moderation) is enforced through the policy engine and a server-side projection, never hidden UI
(FR-ANN-005). Threads support replies (FR-ANN-006) with moderation hooks for shared annotations.
Hexagonal: the ``domain`` layer is pure; infrastructure lives behind ports in ``adapters``.
"""
