"""Northstar runnable processes (API, worker, scheduler).

Each process is a composition root that wires kernel buses to concrete adapters. Infrastructure
libraries (FastAPI, uvicorn, SQLAlchemy) live here and under ``northstar.adapters`` only — never
in the kernel (rule 10).
"""
