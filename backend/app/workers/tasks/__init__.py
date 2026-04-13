"""
Worker task modules — extracted from the aggregation_worker monolith
as part of the Phase Ω⁶ elite hardening sprint.

Each task module exports:
  - a `run(db_session)` function the orchestrator calls
  - an `is_due()` gate that decides whether this cycle should run it

This split is non-destructive: aggregation_worker still imports the
module functions and calls them from its main loop. The benefit is
isolation — a task module can be tested in isolation, replaced, or
moved to its own worker process later.
"""
