"""Aggregates, invariants and scoring. Pure — no I/O, no network calls.

Aggregates are left folds over event lists, which is what makes replay (NFR-ES-3)
and the unit tests in specs/06-tests.md cheap.

scoring.py owns every arithmetic step: quality aggregation, threshold checks,
the quality floor, price scoring and ranking. No LLM participates (NFR-DET-1).
"""
