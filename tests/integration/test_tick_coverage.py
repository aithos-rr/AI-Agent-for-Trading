"""Tick coverage KPI gate for invariant #15 (PRD §9.7).

Invariant #15 requires that every 15-minute tick produces exactly one run per
registered model (4 models → 4 runs), each carrying a recorded status. This is
the behaviour gate: it seeds an experiment + 4 models, inserts 4 Run rows for a
single tick_id, then runs the KPI aggregation
``SELECT tick_id, count(*) FROM runs GROUP BY tick_id`` and asserts the tick has
exactly 4 rows, each with a non-null status.

The column-existence precondition lives in invariant_coverage.py as a plain
supplementary unit test (test_tick_coverage_schema) — it is not the inv #15 gate.
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aiat.db.models.context_snapshot import ContextSnapshot
from aiat.db.models.experiment import Experiment
from aiat.db.models.model import Model
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.models.run import Run

_TICK_ID = "2026-01-15T12:00:00"
_TICK_AT = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
_SCHEMA_VERSION = "v2"
_GIT_SHA = "abc1234"
_PT_TEXT = "You are a trading agent."
_PT_HASH = hashlib.sha256(_PT_TEXT.encode()).hexdigest()

# The 4 registered models for a tick (one run each → 4 runs per tick).
_MODEL_SPECS = (
    ("openai", "gpt-4o", "premium", "USA"),
    ("anthropic", "claude-3-5-sonnet", "premium", "USA"),
    ("deepseek", "deepseek-chat", "cheap_alt", "CN"),
    ("qwen", "qwen-max", "cheap_alt", "CN"),
)
# A per-model status; all non-null (mix of terminal statuses is realistic).
_STATUSES = ("success", "success", "partial", "failed")


@pytest.mark.invariant("15")
async def test_tick_has_exactly_four_runs_with_status(db_session: AsyncSession) -> None:
    """One tick yields exactly 4 runs (one per model), each with a non-null status (inv #15)."""
    exp_id = uuid.uuid4()
    snap_id = uuid.uuid4()

    db_session.add(
        Experiment(
            id=exp_id,
            name=f"test-exp-{exp_id.hex[:8]}",
            started_at=datetime.now(UTC),
            git_commit_sha=_GIT_SHA,
            config_snapshot={},
        )
    )
    await db_session.flush()

    model_ids: list[str] = []
    for provider, model_name, tier, geography in _MODEL_SPECS:
        model_id = f"{provider}-{uuid.uuid4().hex[:8]}"
        model_ids.append(model_id)
        db_session.add(
            Model(
                id=model_id,
                provider=provider,
                model_name_api=model_name,
                tier=tier,
                geography=geography,
                wallet_address=f"0x{uuid.uuid4().hex}",
                pricing_input_usd_per_1m=Decimal("5.000000"),
                pricing_output_usd_per_1m=Decimal("15.000000"),
            )
        )
    await db_session.flush()

    db_session.add(
        PromptTemplate(
            sha256_hash=_PT_HASH,
            label=f"test-pt-{uuid.uuid4().hex[:8]}",
            template_text=_PT_TEXT,
            confidence_def="Probability that the action yields positive PnL.",
            controlled_signals=[],
        )
    )
    db_session.add(
        ContextSnapshot(
            id=snap_id,
            experiment_id=exp_id,
            tick_id=_TICK_ID,
            tick_at=_TICK_AT,
            context_hash="deadbeef",
            context_json={},
            source_timestamps={},
            build_duration_ms=100,
        )
    )
    await db_session.flush()

    # 4 runs for the SAME tick_id, one per model. scheduled_for is identical
    # across models (they share the tick); the runs uniqueness key is
    # (experiment_id, model_id, scheduled_for), so distinct model_ids keep them unique.
    for model_id, status in zip(model_ids, _STATUSES, strict=True):
        db_session.add(
            Run(
                id=uuid.uuid4(),
                experiment_id=exp_id,
                model_id=model_id,
                tick_id=_TICK_ID,
                scheduled_for=_TICK_AT,
                run_started_at=datetime.now(UTC),
                run_completed_at=datetime.now(UTC) + timedelta(seconds=30),
                status=status,
                prompt_template_hash=_PT_HASH,
                rendered_prompt_hash="aabbcc",
                context_snapshot_id=snap_id,
                schema_version=_SCHEMA_VERSION,
                git_commit_sha=_GIT_SHA,
            )
        )
    await db_session.flush()

    # KPI aggregation: SELECT tick_id, count(*) FROM runs GROUP BY tick_id
    # (scoped to this experiment so a session-shared DB stays deterministic).
    result = await db_session.execute(
        select(Run.tick_id, func.count()).where(Run.experiment_id == exp_id).group_by(Run.tick_id)
    )
    coverage = result.all()

    assert len(coverage) == 1, f"expected a single tick group, got {coverage!r}"
    tick_id, n_runs = coverage[0]
    assert tick_id == _TICK_ID
    assert n_runs == 4, f"tick {tick_id} must have exactly 4 runs (one per model), got {n_runs}"

    # Every run for the tick must carry a non-null status.
    statuses = (
        (
            await db_session.execute(
                select(Run.status).where(Run.experiment_id == exp_id, Run.tick_id == _TICK_ID)
            )
        )
        .scalars()
        .all()
    )
    assert len(statuses) == 4
    assert all(s is not None for s in statuses), f"every run must record a status, got {statuses!r}"
