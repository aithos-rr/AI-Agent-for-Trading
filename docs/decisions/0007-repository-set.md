# ADR-0007: Set repository — §7.6 autoritativo, niente `ledger.py`

**Data**: 2026-06-13
**Status**: accepted
**Milestone**: M1, M3, M4, M5
**PRD reference**: §2.2 (struttura cartelle), §7.6 (repository pattern), §5 inv #4
**Closes deferral**: none (correzione di incoerenza interna al PRD)

## Contesto

Il `PRD_V2.md` descrive il set di repository in due punti che divergono:

- §2.2 (Vista repository / struttura cartelle) elenca in `db/repositories/` **4 file**:
  `decisions.py`, `positions.py`, `snapshots.py`, `ledger.py`.
- §7.6 (Repository pattern, aggiornato dai fix B.5 di peer-review) definisce **8
  repository**: `DecisionsRepository`, `PositionsRepository`, `SnapshotsRepository`,
  `RunsRepository`, `OutcomesRepository`, `ContextBuildRepository`,
  `BaselineRepository`, `TaxSimulationRepository` — e **nessun** `LedgerRepository`.

§2.2 è il residuo di una versione iniziale; §7.6 riflette il disegno finale dopo
l'introduzione delle tabelle `context_build_runs`, `baseline_configs`,
`baseline_equity_snapshots`, `tax_sim_periods` e la decisione architetturale
sull'atomicità del cost ledger.

In particolare, il `ledger.py` di §2.2 presupponeva un `LedgerRepository` separato
per `cost_events`. Ma l'invariante #4 (cost ledger persistito DOPO la decision, nella
**stessa transazione**) implica che `cost_events` venga scritto da `DecisionsRepository`
all'interno della transazione atomica decision+actions+cost+llm_invocations — non da
un repository separato che romperebbe l'atomicità.

## Decisione

**§7.6 è la fonte autoritativa per il set di repository.** Si creano gli **8 repository**
di §7.6. Il file `ledger.py` di §2.2 **non viene creato**: i `cost_events` sono persistiti
atomicamente da `DecisionsRepository` (coerente con invariante #4).

Mapping repository → bounded context:

| Repository | File | Tabelle gestite | Milestone |
|------------|------|-----------------|-----------|
| DecisionsRepository | `decisions.py` | decisions, decision_actions, cost_events, llm_invocations | M5 |
| PositionsRepository | `positions.py` | positions, orders, fee_events | M4 |
| SnapshotsRepository | `snapshots.py` | account_snapshots, (context read) | M5 |
| RunsRepository | `runs.py` | runs | M5 |
| OutcomesRepository | `outcomes.py` | outcomes | M5 |
| ContextBuildRepository | `context_build.py` | context_snapshots, context_build_runs | M3 |
| BaselineRepository | `baselines.py` | baseline_configs, baseline_equity_snapshots | M5 |
| TaxSimulationRepository | `tax_simulation.py` | tax_sim_periods | M5 |

## Conseguenze

### Positive
- Atomicità del cost ledger preservata (invariante #4): nessun `LedgerRepository`
  separato che scriverebbe `cost_events` fuori dalla transazione della decision.
- Repository per bounded context coerenti con le 20 tabelle (vedi ADR-0006).

### Negative
- Il testo §2.2 del PRD resta con il set vecchio (4 file incl. `ledger.py`) — non
  modificato perché il PRD è frozen. Questo ADR è la correzione tracciabile.

### Neutre
- §7.6 prevale su §2.2 ovunque divergano sul layer repository.

## Alternative considerate

### Alternativa A: creare anche `ledger.py` come da §2.2
- Scartata: un `LedgerRepository` separato per `cost_events` violerebbe l'invariante
  #4 (persistenza atomica nella stessa transazione della decision). §2.2 è obsoleto
  su questo punto.

### Alternativa B: modificare il PRD §2.2
- Scartata: PRD frozen. Correzione via ADR.

## Test gating

I test integration per repository (`test_db_repositories_decisions.py`,
`test_db_repositories_positions.py`, `test_db_repositories_snapshots.py`) verificano
che `cost_events` sia persistito da `DecisionsRepository` in transazione atomica
(rollback se una action fallisce validazione → nessun `cost_event` orfano).

## Propagazione

- [x] Documentata la decisione in questo ADR
- [ ] M3-T08: ContextBuildRepository
- [ ] M4-T05: PositionsRepository
- [ ] M5-T01: DecisionsRepository (con cost_events atomico)
- [ ] M5-T02a/b/c: SnapshotsRepository, RunsRepository, OutcomesRepository, BaselineRepository, TaxSimulationRepository
- [ ] `ledger.py` NON creato
- [ ] (PRD non modificato: frozen)
