"""Non-LLM baseline equity computation (RESEARCH §3.3, ADR-0036).

``compute`` holds the pure per-tick strategy logic for the three pre-registered baselines
(cash / buy-and-hold / naive EMA-cross momentum); ``runner`` is the DB glue shared by the
live orchestrator step and ``scripts/compute_baselines.py`` (catch-up/backfill).
"""
