"""Seed the database for an AIAT experiment (M7 step 4 / M5-T14 / M6 prerequisite).

Populates everything the agent startup checks A1-A10 and the orchestrator
``_check_active_experiment`` require, so the services can boot:

  - 1 experiment row (A:_check_active_experiment)
  - 4 models (A1/A2/A3): the D1 structural ids from ADR-0020
  - 1 prompt_template (A5): from the ratified ``src/aiat/prompts/trading_v1.md`` +
    the binding confidence definition (RESEARCH §2.1) + the controlled-signal vocabulary
  - 3 baseline_configs (A10): buy_and_hold, cash, naive_momentum_ema_20_50

Design notes:
  - **Single seed, no separate register_prompt_template.py.** The PRD names A5's template
    registration as a separate concern, but registering the template *inside* this seed is
    simpler and less fragile than two scripts that must stay consistent. If a future need
    arises to register a NEW template without re-seeding, extract it then.
  - **Idempotent**: every entity is get-or-create, so re-running does not duplicate.
  - **Parametrized**: no secrets hardcoded. The 4 wallet addresses come from env vars;
    the concrete ``model_name_api`` are PLACEHOLDERS frozen at the M6 seed (D1, ADR-0020) —
    override per model via env when the real models are chosen.
  - ``--dry-run`` computes and prints the full plan (including the prompt-template hash to
    paste into the agent ``.env``) WITHOUT touching the database.

Usage:
    # preview (no DB), with placeholder wallets:
    AIAT_SEED_WALLET_USA_PREMIUM=0x... AIAT_SEED_WALLET_USA_CHEAP=0x... \\
    AIAT_SEED_WALLET_CN_PREMIUM=0x...  AIAT_SEED_WALLET_CN_CHEAP=0x... \\
    uv run python scripts/seed_experiment.py --dry-run

    # real seed (writes to AIAT_DATABASE_URL):
    AIAT_DATABASE_URL=postgresql+asyncpg://... <wallets...> \\
    uv run python scripts/seed_experiment.py
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select

from aiat.config.pricing import load_pricing_for_model
from aiat.context.controlled_signals import CONTROLLED_SIGNALS
from aiat.db.models.experiment import Experiment
from aiat.db.models.model import Model
from aiat.db.models.prompt_template import PromptTemplate
from aiat.db.repositories.baselines import BaselineRepository
from aiat.db.session import get_db_session

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_PATH = _REPO_ROOT / "src" / "aiat" / "prompts" / "trading_v1.md"
_RESEARCH_PATH = _REPO_ROOT / "docs" / "RESEARCH_DESIGN.md"

TEMPLATE_LABEL = "trading_v1"
DEFAULT_EXPERIMENT_NAME = "aiat-v2-experiment"


class SeedError(RuntimeError):
    """Raised on a misconfiguration that prevents seeding (missing env, missing source)."""


@dataclass(frozen=True)
class ModelSpec:
    """Structural spec of one D1 model (ADR-0020). model_name_api frozen at seed."""

    model_id: str
    provider: str
    geography: str
    tier: str
    wallet_env: str
    name_env: str
    default_name_api: str


# ADR-0020: 4 models, one provider per slot, tier = absolute market list price.
MODEL_SPECS: tuple[ModelSpec, ...] = (
    ModelSpec(
        "usa-premium",
        "anthropic",
        "USA",
        "premium",
        "AIAT_SEED_WALLET_USA_PREMIUM",
        "AIAT_SEED_MODEL_NAME_USA_PREMIUM",
        "PLACEHOLDER_usa-premium_set_at_seed_M6",
    ),
    ModelSpec(
        "usa-cheap",
        "openai",
        "USA",
        "cheap_alt",
        "AIAT_SEED_WALLET_USA_CHEAP",
        "AIAT_SEED_MODEL_NAME_USA_CHEAP",
        "PLACEHOLDER_usa-cheap_set_at_seed_M6",
    ),
    ModelSpec(
        "cn-premium",
        "qwen",
        "CN",
        "premium",
        "AIAT_SEED_WALLET_CN_PREMIUM",
        "AIAT_SEED_MODEL_NAME_CN_PREMIUM",
        "PLACEHOLDER_cn-premium_set_at_seed_M6",
    ),
    ModelSpec(
        "cn-cheap",
        "deepseek",
        "CN",
        "cheap_alt",
        "AIAT_SEED_WALLET_CN_CHEAP",
        "AIAT_SEED_MODEL_NAME_CN_CHEAP",
        "PLACEHOLDER_cn-cheap_set_at_seed_M6",
    ),
)

# A10 (lifecycle.py) requires exactly these three baseline_name rows.
# naive_momentum params per PRD §12 M-baselines (ema 20/50, SL 3%, TP 6%, leverage 3).
BASELINE_CONFIGS: dict[str, dict[str, Any]] = {
    "buy_and_hold": {
        "strategy": "buy_and_hold",
        "allocation": "equal_weight",
        "symbols": ["BTC", "ETH", "SOL"],
    },
    "cash": {"strategy": "cash"},
    "naive_momentum_ema_20_50": {
        "ema_fast": 20,
        "ema_slow": 50,
        "stop_loss_pct": 0.03,
        "take_profit_pct": 0.06,
        "leverage": 3,
    },
}


@dataclass(frozen=True)
class ModelPlan:
    spec: ModelSpec
    wallet_address: str
    model_name_api: str
    pricing: dict[str, Decimal]


@dataclass(frozen=True)
class SeedPlan:
    experiment_id: str
    experiment_name: str
    git_sha: str
    template_label: str
    template_hash: str
    template_text: str
    confidence_def: str
    controlled_signals: list[str]
    models: list[ModelPlan]
    baselines: dict[str, dict[str, Any]]


def _out(message: str = "") -> None:
    """Print user-facing output (CLI tool; structlog is for the runtime services)."""
    print(message)  # noqa: T201


def _git_head_sha() -> str:
    """Return the current git HEAD sha, or 'unknown' if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=_REPO_ROOT,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    return result.stdout.strip()


def _template_text() -> str:
    """Load the ratified template, stripping the leading BOZZA HTML comment marker."""
    if not _PROMPT_PATH.exists():
        raise SeedError(f"prompt template not found: {_PROMPT_PATH}")
    raw = _PROMPT_PATH.read_text(encoding="utf-8")
    # The leading "<!-- BOZZA ... -->" comment is a draft marker, not part of the prompt.
    stripped = re.sub(r"^<!--.*?-->\s*", "", raw, count=1, flags=re.DOTALL)
    return stripped.strip()


def _confidence_def_from_research() -> str:
    """Extract the binding confidence definition verbatim from RESEARCH §2.1.

    Sourced from the frozen RESEARCH document (not hand-retyped): locate the §2.1
    section and return the text inside its ``> *"..."*`` blockquote.
    """
    if not _RESEARCH_PATH.exists():
        raise SeedError(f"RESEARCH document not found: {_RESEARCH_PATH}")
    text = _RESEARCH_PATH.read_text(encoding="utf-8")
    parts = text.split("### 2.1", 1)
    if len(parts) < 2:
        raise SeedError("RESEARCH §2.1 heading ('### 2.1') not found")
    section = re.split(r"\n#{2,} ", parts[1], maxsplit=1)[0]
    match = re.search(r'>\s*\*"(.+?)"\*', section, flags=re.DOTALL)
    if match is None:
        raise SeedError('confidence definition blockquote (> *"..."*) not found in §2.1')
    return match.group(1).strip()


def _compute_template_hash(
    template_text: str, confidence_def: str, controlled_signals: list[str]
) -> str:
    """SHA-256 of the canonical template payload (same pattern as baselines.py:48-49)."""
    canonical = json.dumps(
        {
            "template_text": template_text,
            "confidence_def": confidence_def,
            "controlled_signals": controlled_signals,
        },
        sort_keys=True,
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def build_plan(args: argparse.Namespace) -> SeedPlan:
    """Assemble the full seed plan (pure: no DB access)."""
    template_text = _template_text()
    confidence_def = _confidence_def_from_research()
    controlled_signals = sorted(CONTROLLED_SIGNALS)
    template_hash = _compute_template_hash(template_text, confidence_def, controlled_signals)

    models: list[ModelPlan] = []
    missing_wallets: list[str] = []
    for spec in MODEL_SPECS:
        wallet = os.environ.get(spec.wallet_env)
        if not wallet:
            missing_wallets.append(spec.wallet_env)
            continue
        name_api = os.environ.get(spec.name_env, spec.default_name_api)
        models.append(
            ModelPlan(
                spec=spec,
                wallet_address=wallet,
                model_name_api=name_api,
                pricing=load_pricing_for_model(spec.model_id),
            )
        )
    if missing_wallets:
        raise SeedError(
            "Missing required wallet env vars (set one per D1 model):\n  "
            + "\n  ".join(missing_wallets)
        )

    experiment_id = (
        args.experiment_id or os.environ.get("AIAT_SEED_EXPERIMENT_ID") or str(uuid.uuid4())
    )
    git_sha = args.git_sha or os.environ.get("AIAT_SEED_GIT_SHA") or _git_head_sha()

    return SeedPlan(
        experiment_id=experiment_id,
        experiment_name=args.experiment_name,
        git_sha=git_sha,
        template_label=TEMPLATE_LABEL,
        template_hash=template_hash,
        template_text=template_text,
        confidence_def=confidence_def,
        controlled_signals=controlled_signals,
        models=models,
        baselines=BASELINE_CONFIGS,
    )


async def apply_seed(plan: SeedPlan, database_url: str) -> str:
    """Write the plan to the DB idempotently (get-or-create). Returns the experiment_id used."""
    factory = get_db_session(database_url)
    async with factory() as session:
        # Experiment: get-or-create by unique name.
        existing = (
            await session.execute(select(Experiment).where(Experiment.name == plan.experiment_name))
        ).scalar_one_or_none()
        if existing is not None:
            experiment_id = str(existing.id)
        else:
            experiment_id = plan.experiment_id
            session.add(
                Experiment(
                    id=uuid.UUID(experiment_id),
                    name=plan.experiment_name,
                    started_at=datetime.now(UTC),
                    git_commit_sha=plan.git_sha,
                    config_snapshot={
                        "seeded_by": "scripts/seed_experiment.py",
                        "template_label": plan.template_label,
                        "template_hash": plan.template_hash,
                        "model_ids": [m.spec.model_id for m in plan.models],
                        "git_sha": plan.git_sha,
                    },
                )
            )
            await session.flush()

        # Models: get-or-create by id (PK).
        for mp in plan.models:
            if await session.get(Model, mp.spec.model_id) is None:
                session.add(
                    Model(
                        id=mp.spec.model_id,
                        provider=mp.spec.provider,
                        model_name_api=mp.model_name_api,
                        tier=mp.spec.tier,
                        geography=mp.spec.geography,
                        wallet_address=mp.wallet_address,
                        pricing_input_usd_per_1m=mp.pricing["input"],
                        pricing_output_usd_per_1m=mp.pricing["output"],
                        pricing_reasoning_usd_per_1m=mp.pricing["reasoning"],
                    )
                )
        await session.flush()

        # Prompt template: get-or-create by sha256 hash (PK).
        if await session.get(PromptTemplate, plan.template_hash) is None:
            session.add(
                PromptTemplate(
                    sha256_hash=plan.template_hash,
                    label=plan.template_label,
                    template_text=plan.template_text,
                    confidence_def=plan.confidence_def,
                    controlled_signals=plan.controlled_signals,
                )
            )
        await session.flush()

        # Baselines: get-or-create via the repository (A10 needs all three).
        repo = BaselineRepository(session)
        for name, config in plan.baselines.items():
            if await repo.get_baseline_config(experiment_id, name) is None:
                await repo.register_baseline_config(experiment_id, name, config)

        await session.commit()
    return experiment_id


def print_summary(plan: SeedPlan, experiment_id: str, *, dry_run: bool) -> None:
    """Print the seed result, including the .env line for the agents."""
    _out("=" * 70)
    _out("AIAT seed summary" + ("  [DRY-RUN — nothing written]" if dry_run else ""))
    _out("=" * 70)
    _out(f"experiment_id   = {experiment_id}")
    _out(f"experiment_name = {plan.experiment_name}")
    _out(f"git_commit_sha  = {plan.git_sha}")
    _out("")
    _out("Prompt template:")
    _out(f"  label = {plan.template_label}")
    _out("  --> paste this line into the agent .env (must match A5 / settings):")
    _out(f"      AIAT_PROMPT_TEMPLATE_HASH={plan.template_hash}")
    _out("")
    _out("Models (model_id | provider | geo/tier | wallet | model_name_api):")
    for mp in plan.models:
        _out(
            f"  {mp.spec.model_id:<12} | {mp.spec.provider:<9} | "
            f"{mp.spec.geography}/{mp.spec.tier:<9} | {mp.wallet_address} | {mp.model_name_api}"
        )
    _out("")
    _out(f"Baselines: {', '.join(plan.baselines)}")
    if dry_run:
        _out("")
        _out("[DRY-RUN] no database connection was opened.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed the DB for an AIAT experiment.")
    parser.add_argument(
        "--experiment-name",
        default=os.environ.get("AIAT_SEED_EXPERIMENT_NAME", DEFAULT_EXPERIMENT_NAME),
        help="Unique experiment name (get-or-create key). Default: %(default)s",
    )
    parser.add_argument(
        "--experiment-id",
        default=None,
        help="Experiment UUID for creation (default: AIAT_SEED_EXPERIMENT_ID env, else random).",
    )
    parser.add_argument(
        "--git-sha",
        default=None,
        help="git_commit_sha to record (default: env AIAT_SEED_GIT_SHA or current git HEAD).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the plan (incl. template hash) without writing to the DB.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    plan = build_plan(args)

    if args.dry_run:
        print_summary(plan, plan.experiment_id, dry_run=True)
        return

    database_url = os.environ.get("AIAT_DATABASE_URL")
    if not database_url:
        raise SeedError("AIAT_DATABASE_URL must be set for a real seed (use --dry-run to preview).")
    experiment_id = asyncio.run(apply_seed(plan, database_url))
    print_summary(plan, experiment_id, dry_run=False)


if __name__ == "__main__":
    main()
