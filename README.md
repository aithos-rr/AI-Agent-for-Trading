<div align="center">

<img src="asset/aithos-favicon.png" alt="AIAT·V2 mascot — the golden circuit-board owl of Aithos, gold linework with blue eyes" width="200" />

# AIAT·V2 — AI Agent for Trading

### Four LLMs · $1,000 each · autonomous crypto perpetuals on the Hyperliquid testnet

**_One decision every 15 minutes — same prompt, same capital, same market: different minds._**

<p>
<img src="https://img.shields.io/badge/THESIS-SAPIENZA%20·%20PHILOSOPHY%20%26%20AI-7c3aed?style=for-the-badge" alt="Sapienza thesis" />
<img src="https://img.shields.io/badge/HYPERLIQUID-TESTNET-f59e0b?style=for-the-badge" alt="Hyperliquid testnet" />
<a href="https://dashboard-production-898d.up.railway.app/"><img src="https://img.shields.io/badge/🔴%20LIVE-DASHBOARD-16a34a?style=for-the-badge" alt="Live dashboard" /></a>
</p>

<p>
<img src="https://img.shields.io/badge/python-3.12-3776ab?logo=python&logoColor=white" alt="Python 3.12" />
<img src="https://img.shields.io/badge/Railway-6%20services-0B0D0E?logo=railway&logoColor=white" alt="Railway, 6 services" />
<img src="https://img.shields.io/badge/PostgreSQL-16-336791?logo=postgresql&logoColor=white" alt="PostgreSQL 16" />
<img src="https://img.shields.io/badge/governance-34%20ADRs-blue" alt="34 Architecture Decision Records" />
<img src="https://img.shields.io/badge/tick-15%20min-blue" alt="15 minute tick" />
<img src="https://img.shields.io/badge/tests-808%20passing-brightgreen" alt="808 tests" />
</p>

**🦉 [Watch them trade live →](https://dashboard-production-898d.up.railway.app/)**

</div>

---

**AIAT·V2** is a thesis experiment (Philosophy & Artificial Intelligence, Sapienza
University of Rome) that gives **four LLM agents $1,000 each** and lets them trade crypto
perpetual futures autonomously on the **Hyperliquid testnet** — a decision every
15 minutes, guardrails, stop-losses, and a fully persisted audit trail.

| | Model | Provider | Tier |
|---|---|---|---|
| 🟡 | Claude Opus 4.8 | Anthropic | USA · premium |
| 🟢 | GPT 4.1 mini | OpenAI | USA · cheap |
| 🟣 | Qwen3.7-Max | Alibaba | CN · premium |
| 🔵 | DeepSeek V4 Flash | DeepSeek | CN · cheap |

## What this is

Not a trading-bot showcase — a **comparative behavioral study**. The four models get the
identical prompt, the identical market context, and the identical capital; what differs is
the mind. The experiment measures **behavioral differences between providers** — trade
frequency, risk appetite, reasoning style, reliability (schema compliance, fallbacks), and
the true cost of intelligence — across two pre-registered axes: **USA vs CN** and
**premium vs cheap**. It is not about "who gets rich": it's testnet money, and the
interesting output is the audit trail, not the PnL.

## Architecture in one breath

```
                          Hyperliquid testnet
             ⇅ orders · fills             ⇅ market data · account state
   ┌───────────────────────────┐   ┌────────────────────────────────────┐
   │     4× agent-<provider>   │   │        context-orchestrator        │
   │  one LLM + one wallet each│   │  one context_snapshot per 15-min   │
   │  guardrails → execution   │   │  tick + ClosureReconciler (books   │
   │                           │   │  SL/TP closures hit between ticks) │
   └─────────────┬─────────────┘   └─────────────────┬──────────────────┘
                 │ decisions · positions · costs     │ snapshots · closures
                 ▼                                   ▼
             PostgreSQL 16 (Railway) — the single audit trail
                              │ reads (SELECT-only role)
                              ▼
                    AIAT·V2 Dashboard (separate repo)
```

- **One codebase, six services** — the same image runs as orchestrator or agent via
  `AIAT_SERVICE_ROLE`; Postgres is the sixth Railway service
- **Market-context parity** — only the orchestrator talks to external sources; the four
  agents read the same frozen `context_snapshot`, nobody fetches anything mid-run
- **Guardrails before the exchange** — leverage/size clamps, forced HOLD, testnet-only
  startup check, `Decimal` everywhere money flows
- **ClosureReconciler** — stop-loss/take-profit closures that fire *between* ticks are
  detected on-chain and booked at orchestrator level, keeping DB and chain in agreement
- **Cross-model isolation** — every agent query is filtered by `model_id`, enforced by
  end-to-end tests with a DB trap

## Scientific rigor

- **Certified prompt** — all four models receive the same prompt template; its hash is
  persisted with every run and frozen for the whole experiment
- **Frozen blueprint + ADR governance** — the PRD is tagged `prd-v2-frozen`; every
  deviation or evolutive decision is an Architecture Decision Record in
  [`docs/decisions/`](docs/decisions/) (34 accepted so far)
- **Pre-registered baselines** — cash, buy & hold, and EMA-momentum curves are declared in
  [`docs/RESEARCH_DESIGN.md`](docs/RESEARCH_DESIGN.md) before the run, and computed from
  the same context snapshots the models see
- **DB ↔ chain reconciliation** — positions are verified fill-by-fill against on-chain
  `userFills`; divergences are detected, root-caused, and documented — see the
  [M6.1 methodological note](docs/NOTA-METODOLOGICA-M6.1.md) for full transparency on the
  shakedown run

## Repository structure

```
src/aiat/       one Python package, five service roles (AIAT_SERVICE_ROLE dispatch):
                domain · context · llm · execution · orchestration · baselines ·
                db · config · observability · prompts
alembic/        schema migrations — the database is never edited by hand
docs/           PRD_V2 (frozen blueprint) · RESEARCH_DESIGN · M6.1 methodological note ·
                decisions/ (ADRs) · runbooks
scripts/        one-shot ops: experiment seed, fee backfill, audited data repairs, baselines
tests/          808 tests: unit · integration · e2e (isolation, invariants) · VCR cassettes
tools/          gate_check.sh — milestone gate runner
docker/         multi-stage Dockerfile: one image, role picked via env
legacy/         the V1 prototype, preserved
asset/          README media (mascot)
```

## Dashboard

The experiment ships with a live observability layer — equity race with baselines,
decision ledger, side-by-side reasoning, cost of intelligence, reliability — built as a
separate read-only repo:

- **Repo**: [aithos-rr/AI-Agent-for-Trading-Dashboard](https://github.com/aithos-rr/AI-Agent-for-Trading-Dashboard)
- **Live**: <https://dashboard-production-898d.up.railway.app/>

## The thesis behind it

> *Comparative study of the decision quality of 4 LLMs (USA / CN × premium / cheap) as
> autonomous trading agents: same prompt, same capital, same market — different minds.*

The full research design (3 research questions, hypotheses, baselines) lives in
[`docs/RESEARCH_DESIGN.md`](docs/RESEARCH_DESIGN.md); the technical blueprint in
[`docs/PRD_V2.md`](docs/PRD_V2.md).

---

<div align="center">

**Aithos · Sapienza Università di Roma** · Hyperliquid testnet · no real money involved

<sub>⚠️ Research project. Nothing here is financial advice — four language models managing
$1,000 of fake money each is a thesis, not a hedge fund.</sub>

</div>
