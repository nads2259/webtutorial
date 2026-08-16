"""Support module: governed contact/support cases with validated intake and staff data minimization.

Owns the ``northstar_support`` schema (docs/29 §6, FR-SUP-001..003). Contact/support requests are
governed records, not unstructured email side effects: intake VALIDATES input (rejecting
malformed/oversized/injection-shaped submissions), cases carry ownership + a lifecycle, and support
staff see only the MINIMUM data by default. Any elevated/privileged read requires an audited,
deny-by-default, time-bounded support-access grant; an unauthorized broad read is refused and
logged.
"""
