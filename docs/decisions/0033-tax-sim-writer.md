# ADR-0033: Tax-sim writer — job orchestrator + rate 0.33 come override di config

**Data**: 2026-07-11
**Status**: accepted
**Milestone**: M6.2 (pre-M7)
**PRD reference**: §4.3 (simulazione fiscale italiana), §7.6
**Closes deferral**: none (chiude il gap «writer assente» analogo a quello dei baseline)

## Contesto

`TaxSimulationRepository.compute_and_persist_period` (aggregazione outcomes → riga
`tax_sim_periods`, regola italiana §4.3: `taxable_base = max(0, gross − fees − funding)`,
`tax_due = base × rate`) **esiste ma non ha alcun caller di produzione**: come i baseline, era
raggiungibile solo dai test. Quindi `tax_sim_periods` resta vuoto in esercizio. Inoltre il default
di schema `tax_sim_periods.tax_rate_pct` è **0.26** (server_default), mentre il regime applicato in
questo studio ai derivati crypto con leva è **0.33**.

## Decisione

Implementare un **runner + job orchestrator giornaliero** che chiama il repository esistente:

- **`TaxSimRunner`** (`src/aiat/orchestration/tax_sim_runner.py`): a ogni run calcola il **periodo
  chiuso più recente** (`compute_closed_period(now, mode)`), poi per ogni modello che ha `outcomes`
  nell'esperimento aggrega gli outcomes del periodo (bucket per `Outcome.created_at`, colonna
  indicizzata) e persiste una riga `tax_sim_periods` via `compute_and_persist_period`.
- **Rate come override di config, NON cambio di schema**: `TaxSimRunner` passa
  `tax_rate_pct=settings.tax_rate_pct` (default **0.33**) esplicitamente a ogni riga, quindi il
  server_default 0.26 non viene mai usato → **nessuna migration**. `AIAT_TAX_RATE_PCT` overridabile.
- **Periodo configurabile**: `AIAT_TAX_PERIOD` ∈ {`daily`, `quarter`} (default `quarter` per
  l'esperimento; `daily` per lo smoke M6.2, feedback rapido). `daily` → giorno pieno precedente;
  `quarter` → trimestre solare precedente (Gen → Q4 anno prima).
- **Idempotenza** sulla UNIQUE `(experiment_id, model_id, quarter_label)` via *check-then-skip*:
  eseguire il job giornaliero in modalità `quarter` ricomputa l'ultimo trimestre chiuso e lo salta se
  già presente.
- **Wiring**: `build_scheduler_for_orchestrator(tax_sim_job=...)` (CronTrigger giornaliero 00:05 UTC);
  `__main__._build_tax_sim_job`. `pnl_net_fee_funding_tax_sim_usd` sull'`Outcome` resta 0 (ADR-0014,
  invariato): questo ADR aggrega, non annota il per-outcome.

## Conseguenze

### Positive
- `tax_sim_periods` finalmente popolato in esercizio, con il rate corretto (0.33) senza migration.
- Periodo configurabile (smoke daily vs esperimento quarter) senza cambi di codice.
- Idempotente; sicuro da rieseguire.

### Negative
- Un singolo run copre **solo il periodo chiuso più recente**: un run mancato non fa backfill dei
  periodi precedenti. Accettabile — la tax-sim è un'aggregazione ricomputabile a posteriori (si può
  aggiungere un loop di backfill in seguito).
- Vengono create righe anche per modelli con 0 outcomes nel periodo (riga a 0): scelta di
  completezza (ogni modello partecipante ha un record «nessuna imposta»); nessun effetto fiscale.

### Neutre (trade-off accettati)
- Aggregazione per `created_at` dell'`Outcome` (istante di persistenza), non per `closed_at` della
  posizione: coerente con l'indice `idx_outcomes_model_time` e con la firma esistente del repository.
  Differenza trascurabile (l'outcome è creato alla chiusura).

## Alternative considerate

### Alternativa A: `scripts/compute_tax_sim.py` una-tantum (come i baseline post-esperimento)
- Pro: nessun job runtime; coerente con i docstring esistenti che citano quello script.
- Contro: non produce dati durante lo smoke M6.2; richiede esecuzione manuale.
- Scartata perché: M6.2 vuole il ledger fiscale popolato in continuo per validare la pipeline; un job
  schedulato idempotente lo fa senza intervento e resta valido per M7.

### Alternativa B: migration per cambiare il server_default a 0.33
- Pro: default «corretto» a livello schema.
- Contro: migration su tabella esistente; il rate è comunque scritto esplicitamente dal writer, quindi
  il default non è mai determinante.
- Scartata perché: «migration solo se inevitabile»; l'override di config è sufficiente e reversibile.

## Test gating

- `tests/unit/orchestration/test_tax_sim_runner.py`: `compute_closed_period` (daily, quarter, boundary
  Gennaio→Q4 anno prima).
- `tests/e2e/test_tax_sim_runner.py` (Postgres reale): aggregazione del periodo chiuso (esclusione
  outcomes fuori periodo, `rate=0.33` override, `taxable_base`/`tax_due` corretti), floor a 0 su
  perdita netta, e **idempotenza**. Tripwire: nessun caller esisteva → `tax_sim_periods` restava vuoto.

## Propagazione

- [x] `TaxSimRunner` + `compute_closed_period` in `src/aiat/orchestration/tax_sim_runner.py`
- [x] `ContextOrchestratorSettings.tax_rate_pct` (0.33) + `tax_period`
- [x] Wiring scheduler (`tax_sim_job`) + `__main__._build_tax_sim_job`
- [x] Test unit + e2e
- [ ] (Opzionale) backfill multi-periodo se un run viene mancato
- [ ] Aggiornare `PRD_V2.md` §4.3 con riferimento a questo ADR
