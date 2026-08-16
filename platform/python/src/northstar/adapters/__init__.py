"""Infrastructure adapters (ports & adapters, LAW-12).

Everything under this package MAY import infrastructure libraries (SQLAlchemy, drivers,
provider SDKs). Kernel/domain code MUST NOT import from here directly; it depends on the
kernel ports these adapters implement, wired at the composition edge.
"""
