# CLAUDE.md — AI Trading Agent V2 (Thesis Edition)

> Questo file è la fonte di verità operativa per Claude Code (Sonnet/Opus)
> durante l'implementazione di Phase 5. Leggilo PRIMA di qualunque tool call.

## Ground truth documenti

Il progetto è basato su 5 documenti **frozen** (tag `prd-v2-frozen`,
commit `22d3119`). I documenti vivono in `docs/`:

| Documento | Path | Ruolo |
|-----------|------|-------|
| PRE_PRD | `docs/PRE_PRD.md` | 18 decisioni strategiche |
| Analysis ref repo | `docs/ANALYSIS_REFERENCE_REPO.md` | Pattern da TradingAgents |
| Analysis Figma | `docs/ANALYSIS_FIGMA.md` | Requisiti F1/F2/F3 |
| Research Design | `docs/RESEARCH_DESIGN.md` | Cornice scientifica (3 RQ, ipotesi, baseline) |
| **PRD V2** | `docs/PRD_V2.md` | **Blueprint tecnico completo (§0-§15, 3626 righe)** |

**Regola d'oro**: se hai dubbi su una decisione di design, **NON inventare**.

1. Leggi prima il `docs/PRD_V2.md` (sezione pertinente)
2. Se la risposta non c'è, controlla gli ADR in `docs/decisions/`
3. Se ancora niente, **fermati e chiedi all'utente**, NON dedurre

## Architettura in 1 minuto

5 servizi Python su Railway condividono UN database Postgres:

- 1× `context-orchestrator` (5° servizio): materializza UN `context_snapshot`
  per tick di 15 minuti. Letto dai 4 agent.
- 4× `agent-<provider>` (OpenAI, Anthropic, DeepSeek, Qwen): ciascuno con
  proprio wallet HL testnet, legge `context_snapshot` del tick, invoca LLM,
  applica guardrail, esegue ordini.

Dispatch via `AIAT_SERVICE_ROLE` env var: stesso codice, ruoli diversi
(monorepo, vedi PRD §11.2).

## Stack vincolante

- **Python 3.12+**, `uv` package manager (NO pip, NO poetry, NO conda)
- **Pydantic v2** strict everywhere (NO `dict[str, Any]` nei contratti)
- **SQLAlchemy 2.x** async (`Mapped`/`DeclarativeBase`), `asyncpg` driver
- **Alembic** per migrations (NO modifiche manuali al DB)
- **APScheduler** per cron 15m (NO Railway cron native)
- **langchain-core** + **langchain-openai** + **langchain-anthropic** MINIMAL
  (NO LangGraph, NO LangChain high-level)
- **structlog** JSON logs (NO `print()` runtime — ruff T201 enforced)
- **pytest** + pytest-asyncio + pytest-postgresql + VCR.py per test
- **ruff** + **mypy strict** + **import-linter** in CI
- **Docker** multi-stage, non-root user

NO altre dipendenze senza chiedere all'utente (e poi creare ADR).

## Invarianti non negoziabili (PRD §5, 15 totali)

I 15 invarianti sono tutti ground rule. Questi 5 sono i più critici per il
workflow di sviluppo:

**#1 Isolation cross-model**: ogni query agent filtra
`WHERE model_id = $AIAT_MODEL_ID`. Test gating:
`tests/e2e/test_isolation.py` con RepositorySpy + DB trap.

**#4 Cost ledger atomico**: `LLMClient.invoke()` ritorna `CostEventData` DTO,
persistito DOPO `decisions` nella stessa transazione. **MAI** scrivere
`cost_events` direttamente in `invoke()`.

**#9 No mainnet**: `AIAT_NETWORK=testnet` validato all'avvio. `RuntimeError`
fatal se diverso.

**#12 Decimal per soldi**: `decimal.Decimal` ovunque, MAI `float` per
size/price/fee/PnL. SQLAlchemy `Numeric` columns.

**#13 Parità market context**: il `context_snapshot` è scritto SOLO dal
`context-orchestrator`. Gli agent NON fetchano sorgenti esterne durante la
run. (Portfolio state diverge correttamente per modello — non è un bug.)

Per la lista completa: PRD V2 §5 + invariant coverage matrix in §9.7.

## Workflow di sviluppo

### TDD obbligatorio per moduli core

I moduli in `domain/`, `llm/`, `execution/` richiedono **TDD**:

1. Scrivi il test PRIMA dell'implementazione
2. Verifica che il test fallisca (red)
3. Implementa il minimo per far passare il test (green)
4. Refactor

Coverage target: **95%** su questi moduli (CI gating con
`--cov-fail-under=95`). Per il resto del codice: **80%** globale.

### Sequenza milestones (PRD §12)

Sviluppo IN ORDINE: M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7.

Eccezione: M2 e M3 possono procedere in parallelo dopo M1.

**Non avviare Mn senza completare Mn-1.** Non avviare M7 (esperimento
ufficiale) senza M6 verde (smoke 48h).

Ogni milestone ha un **Definition of Done** esplicito in PRD §12. La
milestone si chiude SOLO quando il DoD è verificato.

### Bounded deferrals (PRD §15.4)

Ci sono 5 decisioni deferite con milestone vincolante di chiusura:

| ID | Decisione | Chiudere entro |
|----|-----------|----------------|
| D1 | Selezione finale 4 modelli LLM | M7 step 4 (seed_experiment) |
| D2 | HOLD/FLAT outcome labeling rule | M4 (OutcomeResolver) |
| D3 | Exception class isinstance() per rate/auth | M2 (LLM abstraction) |
| D4 | Vocabolario `controlled_signals` finale | M3 (ContextOrchestrator) |
| D5 | RSS sources count + lista finale | M3 (collectors/news.py) |

Quando chiudi una di queste decisioni: **crea un ADR** (vedi sotto).

### No commit senza test

Per ogni PR:

- [ ] Tutti i test passano (`uv run pytest`)
- [ ] Coverage globale ≥ 80%, core ≥ 95%
- [ ] `uv run ruff check src tests` clean
- [ ] `uv run ruff format --check src tests` clean
- [ ] `uv run mypy src` clean
- [ ] `uv run import-linter` clean
- [ ] Se è una migration: `alembic upgrade head` + `alembic downgrade base`
      testati su Postgres pulito

### Conventional commits

Pattern:

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `ci`.

### Branch naming

- `feat/<milestone>-<scope>` per nuove feature (es. `feat/m1-domain`)
- `fix/<scope>` per bugfix
- `chore/<scope>` per setup, CI, deps
- `docs/<scope>` per modifiche puramente documentali

## Architecture Decision Records (ADR)

Gli ADR vivono in `docs/decisions/` e documentano:

- Deviazioni dal PRD V2
- Chiusure di bounded deferrals (D1-D5)
- Decisioni implementative con implicazioni durature

**Quando creare un ADR** (regola operativa per Claude Code):

1. Stai chiudendo una deferred decision (D1-D5)? → ADR obbligatorio
2. Stai deviando da una sezione del PRD V2? → ADR obbligatorio
3. Stai prendendo una decisione architetturale nuova (non in PRD)? → ADR obbligatorio
4. Stai facendo un refactor interno o un bugfix? → NO ADR, basta git commit message

Template: `docs/decisions/0000-template.md`.
Indice degli ADR accettati: `docs/decisions/README.md`.

## Stile codice

- Type hints **ovunque** (mypy strict mode)
- Docstring **stile Google** per funzioni pubbliche
- `async def` di default; `def` solo per pure function senza I/O
- NO `from X import *`
- Path imports espliciti: `from aiat.domain.schemas import TradeDecision`
- Decimal literals: `Decimal("0.20")` (string init), MAI `Decimal(0.20)` (float init lossy)
- Logging: `logger.info("event_name", key1=val1)` (structlog kwargs), MAI f-string

## Quando chiedere all'utente

Chiedi conferma esplicita PRIMA di:

- Modificare lo schema DDL (sempre via Alembic migration nuova, mai modifica retroattiva)
- Aggiungere una dipendenza al `pyproject.toml`
- Cambiare un guardrail default in `AgentSettings`
- Toccare i 4 model_id registrati nel seed
- Skip / soft-skip di un test esistente
- Modificare un invariante in PRD §5

Procedi autonomamente per:

- Implementare un modulo seguendo il PRD
- Aggiungere test (sempre incoraggiati)
- Refactoring interno che NON cambia API esposta
- Fix di bug isolati con test che riproduce il bug
- Aggiungere docstring / type hints mancanti

## Comandi frequenti

```bash
# Setup iniziale
uv sync                              # installa deps da pyproject.toml + uv.lock
uv add <package>                     # aggiungi dep (poi chiedi se ok per pyproject)

# Lint & type check
uv run ruff check src tests
uv run ruff format src tests
uv run mypy src
uv run import-linter

# Test
uv run pytest                                      # tutti
uv run pytest tests/unit -v                        # solo unit
uv run pytest tests/unit --cov=src/aiat --cov-report=term-missing
uv run pytest -m "invariant" --invariant-report=invariants.md

# DB
alembic upgrade head                              # applica migrations
alembic revision --autogenerate -m "<desc>"       # nuova migration
alembic downgrade base                            # rollback completo

# Run locale (richiede .env)
python -m aiat                       # legge AIAT_SERVICE_ROLE da env
```

## Sicurezza: cose da NON fare MAI

- ❌ Committare file `.env` o qualunque file con credenziali
- ❌ Hard-code API key o private key nel codice
- ❌ Disabilitare TLS / verify=False su httpx/requests
- ❌ `eval()` / `exec()` su input esterno
- ❌ Wallet HL su mainnet anche solo per test (invariante #9)
- ❌ Modificare `prompt_template_hash` durante run sperimentale (PRD §3.2.1)

## Riferimento rapido

Quando ti perdi:

1. `docs/PRD_V2.md` §X.Y per blueprint
2. `docs/RESEARCH_DESIGN.md` per giustificazione scientifica
3. `docs/decisions/*.md` per decisioni evolutive
4. Git log per storia delle scelte
5. Se ancora confuso → chiedi all'utente

---

*Last updated: M0 setup. Aggiornare quando emergono nuove regole operative
(es. dopo chiusura ADR significativi).*
