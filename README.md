# AI Trading Agent V2 — Thesis Edition

Multi-model autonomous trading agent for Hyperliquid testnet, designed as a comparative study of LLM decision quality.

## Documentation

All design documents live in [`docs/`](docs/):

- [`docs/PRD_V2.md`](docs/PRD_V2.md) — Technical blueprint (§0-§15, ground truth)
- [`docs/RESEARCH_DESIGN.md`](docs/RESEARCH_DESIGN.md) — Scientific framework (3 RQs, hypotheses, baseline)
- [`docs/decisions/`](docs/decisions/) — Architecture Decision Records

## Setup

Requires Python 3.12+ and [`uv`](https://github.com/astral-sh/uv).

```bash
uv sync
```

## Run

```bash
uv run python -m aiat
```

Dispatch is controlled by the `AIAT_SERVICE_ROLE` environment variable (see `.env.example`).

## Development

```bash
uv run ruff check src tests
uv run ruff format src tests
uv run mypy src
uv run pytest
uv run lint-imports
```
