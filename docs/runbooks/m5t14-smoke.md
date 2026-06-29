# Runbook — M5-T14 smoke (real LLM + HL testnet, one agent at a time)

Operational, step-by-step procedure for the M5-T14 smoke. Run in **WSL** (the devcontainer is
firewalled from LLM providers and Hyperliquid). Strategy = **Option 2** (ADR-0022): one agent at
a time, reusing **one** funded testnet wallet, moving its address across the 4 models in turn.

> ⚠️ **The crux of Option 2**: `models.wallet_address` is UNIQUE and A3 requires this agent's
> `AIAT_HL_WALLET_ADDRESS` to equal its model's `models.wallet_address`. Reusing ONE real wallet
> means: **before** assigning the real address to the next model, **reset the previous model's
> address back to a placeholder** — otherwise two models hold the same real address and the
> UNIQUE constraint fails.

## Test order (ADR-0022)

| # | model_id      | provider  | note                              |
|---|---------------|-----------|-----------------------------------|
| 1 | `usa-premium` | anthropic | mature structured output first    |
| 2 | `usa-cheap`   | openai    |                                   |
| 3 | `cn-premium`  | qwen      | OpenAI-compatible (watch format)  |
| 4 | `cn-cheap`    | deepseek  | OpenAI-compatible (watch format)  |

## Prerequisites (once)

- Postgres up and migrated to **alembic 003**: `alembic upgrade head` (startup check A
  `_check_db_connectivity_and_schema` requires version `003`).
- Experiment seeded: `uv run python scripts/seed_experiment.py` with the 4 wallet env vars set
  (placeholders are fine — Option 2 overwrites one model's address at a time). Note the printed
  `experiment_id` and `AIAT_PROMPT_TEMPLATE_HASH`.
- **One funded HL testnet wallet** (faucet) — A6 rejects equity ≤ 0.
- The 4 provider API keys (one used per agent run).
- `psql` connection string (drop the `+asyncpg` driver suffix), e.g.
  `psql "postgresql://postgres:test@localhost:5433/aiat_test"`.

## Per-agent procedure (repeat for each of the 4, in order)

Let `MID` = the model id under test (e.g. `usa-premium`), `PREV` = the previously-tested model
id (none for the first), `REAL_ADDR` = your funded testnet wallet address.

### 1. Move the real wallet to this model in the DB

```bash
# Reset the PREVIOUS model back to a placeholder so REAL_ADDR is held by ONE model only.
psql "$PG" -c "UPDATE models SET wallet_address='placeholder-$PREV' WHERE id='$PREV';"   # skip for the first model
# Assign the real funded address to the model under test.
psql "$PG" -c "UPDATE models SET wallet_address='$REAL_ADDR' WHERE id='$MID';"
# Verify exactly one model has REAL_ADDR:
psql "$PG" -c "SELECT id, provider, wallet_address FROM models ORDER BY id;"
```

### 2. Prepare the agent `.env`

Copy `.env.agent.template` → `.env.agent` and fill, for this model, the three coupled fields:
- `AIAT_MODEL_ID=$MID`
- `AIAT_LLM_PROVIDER=<provider of $MID>` (A2) and the matching `AIAT_<PROVIDER>_API_KEY`
- `AIAT_MODEL_NAME_API=<real model name>`
- `AIAT_HL_WALLET_ADDRESS=$REAL_ADDR` (A3) + `AIAT_HL_WALLET_PRIVATE_KEY=<key>`
- `AIAT_EXPERIMENT_ID` and `AIAT_PROMPT_TEMPLATE_HASH` (A5) from the seed output
- keep `AIAT_HL_CLIENT_IMPL=real`, `AIAT_NETWORK=testnet`, `AIAT_TEMPERATURE=0`, `AIAT_SEED=42`,
  `AIAT_INJECT_DECISION_HISTORY=False`.

### 3. Populate the tick's context_snapshot (orchestrator)

The orchestrator and the agent must run in the **same 15-minute quarter** so they agree on
`tick_id` (`current_tick()` floors to the boundary, inv #13). Run the orchestrator first.

`load_settings()` reads a **fixed `.env`** (env_file=".env"), the same constraint M3-T11 hit —
so swap `.env` per role:

```bash
# --- orchestrator: one manual tick ---
[ -f .env ] && mv .env .env.bak
cp .env.orchestrator .env          # the orchestrator env from M3-T11 (role=context_orchestrator)
uv run python - <<'PY'
import asyncio
from aiat.config.settings import load_settings
from aiat.orchestration.lifecycle import startup_checks
from aiat.__main__ import _build_orchestrator_tick_job
async def go() -> None:
    s = load_settings()
    await startup_checks(s)                      # O1-O4
    job = await _build_orchestrator_tick_job(s)  # type: ignore[arg-type]
    await job()                                  # writes ONE context_snapshot for the current tick
asyncio.run(go())
PY
```

### 4. Run the agent for this tick

```bash
# --- agent: one manual tick (same quarter ⇒ same tick_id ⇒ reads the snapshot above) ---
cp .env.agent .env
uv run python - <<'PY'
import asyncio
from aiat.config.settings import load_settings
from aiat.orchestration.lifecycle import startup_checks
from aiat.__main__ import _build_agent_tick_job
async def go() -> None:
    s = load_settings()
    await startup_checks(s)                # A1-A10 (incl. A6 real wallet equity>0, A7 real LLM "pong")
    job = await _build_agent_tick_job(s)   # type: ignore[arg-type]
    await job()                            # one run_once: LLM → guardrails → testnet orders
asyncio.run(go())
PY
```

Alternative to manual ticks: run `uv run python -m aiat` (real scheduler) under each `.env` and
wait for the `:00/:15/:30/:45` boundaries — slower (~1 tick / 15 min) but exercises the real
scheduler path.

### 5. Repeat ~4 ticks

Re-run steps 3-4 across 4 consecutive quarters (or 4 manual orchestrator+agent pairs in 4
distinct quarters — `tick_id` differs per quarter, so the agent's `runs` UNIQUE
`(experiment_id, model_id, scheduled_for)` is satisfied). For a fast manual smoke, wait ~1 min
between pairs is NOT enough (same quarter ⇒ same tick_id ⇒ duplicate run); cross a 15-min
boundary between ticks, or set the system clock expectation accordingly.

### 6. Verify the dataset for this model

```bash
psql "$PG" <<SQL
\set exp '<EXPERIMENT_ID>'
\set mid '<MID>'
SELECT count(*) AS runs,
       count(*) FILTER (WHERE status='success') AS success,
       count(*) FILTER (WHERE status<>'success') AS non_success
  FROM runs WHERE experiment_id=:'exp' AND model_id=:'mid';
SELECT count(*) AS decisions FROM decisions WHERE experiment_id=:'exp' AND model_id=:'mid';
SELECT symbol, side_executed, count(*) FROM decision_actions
  WHERE experiment_id=:'exp' AND model_id=:'mid' GROUP BY 1,2 ORDER BY 1,2;
SELECT count(*) AS positions FROM positions WHERE experiment_id=:'exp' AND model_id=:'mid';
SELECT order_kind, status, count(*) FROM orders
  WHERE experiment_id=:'exp' AND model_id=:'mid' GROUP BY 1,2 ORDER BY 1,2;
SELECT count(*) AS llm_invocations,
       count(*) FILTER (WHERE fallback_used) AS fallbacks
  FROM llm_invocations WHERE experiment_id=:'exp' AND model_id=:'mid';
SELECT count(*) AS cost_events, sum(cost_usd) AS total_cost_usd
  FROM cost_events WHERE experiment_id=:'exp' AND model_id=:'mid';
SELECT error_kind, count(*) FROM errors
  WHERE experiment_id=:'exp' AND model_id=:'mid' GROUP BY 1;
SQL
```

### 7. Next provider

Go to step 1 with `MID` = next model in the test order and `PREV` = the model just tested.

## What to observe / red flags

- **No fallback parsing**: `llm_invocations.fallback_used` should be `false`. A `true` means the
  provider's structured output didn't validate first try → the V2 prompt or the provider's
  json_schema/tool-use handling needs attention (the confound this smoke is meant to find).
  Hard failures land in `errors` and `runs.status` ∈ {failed, timeout}.
- **3 actions per tick**: `decision_actions` must have one row per BTC/ETH/SOL per run (the
  schema enforces it; absence ⇒ a parsing/validation problem upstream).
- **Guardrails**: check whether size/leverage were clamped or an action was forced to HOLD
  (compare the LLM's raw `decisions` payload vs the persisted `decision_actions` executed fields;
  guardrails are silent by design, inv #8).
- **Positions & triggers**: for LONG/SHORT, a `positions` row plus `orders` of kind `entry`,
  `stop_loss`, `take_profit` (SL/TP are reduce-only triggers resting on HL). Confirm on the HL
  testnet UI that the trigger orders exist.
- **Provider surprises (qwen/deepseek)**: watch for safety refusals or non-conformant JSON
  (risk register S5) — these are scientific data, not bugs, but must be logged.

## Cleanup

After all 4: optionally reset every `models.wallet_address` to a placeholder, and close any
residual testnet position (the smoke leaves SL/TP triggers resting — the agent's
`RealHyperliquidClient` teardown is per-test; flatten manually on HL if needed).

## Limits (declared)

This validates **each agent end-to-end with its real provider for ~4 ticks**, NOT the
**4-agent concurrency** (shared single wallet, sequential). Concurrency invariants are covered
by the automated e2e `tests/e2e/test_context_parity.py` (inv #13) and
`tests/e2e/test_isolation.py` (inv #1); real 4-wallet concurrency is M6.
