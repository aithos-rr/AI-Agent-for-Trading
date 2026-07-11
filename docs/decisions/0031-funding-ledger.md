# ADR-0031: Funding ledger — job orchestrator 8h da HL `userFunding`

**Data**: 2026-07-11
**Status**: accepted
**Milestone**: M6.2 (pre-M7)
**PRD reference**: §4.2 (Flusso 2 — Funding & Outcome Resolution)
**Closes deferral**: none (chiude il gap di implementazione «finding B» dello smoke M6 esteso)

## Contesto

Lo smoke M6 esteso (4 giorni, ~374 run/agent) ha lasciato `funding_events` **a 0 righe**
nonostante posizioni perp aperte per giorni: su Hyperliquid il funding matura ogni ora, quindi
0 righe è impossibile se non per un gap di implementazione. L'analisi (finding B) ha confermato:
la tabella `funding_events` e lo schema esistono (migration `001`), `PositionsRepository.close_position`
**legge** già `sum(funding_amount_usd)` per popolare `outcomes.sum_funding_usd`, ma **nessun
componente scrive** `funding_events` — nessun writer, nessuna chiamata HL funding, nessun job
schedulato. Il PRD §4.2 lo richiede esplicitamente («Job parallelo APScheduler che gira ogni 8 ore
… per ogni position OPEN: fetch funding rate da HL, crea funding_event row»); non è tra i deferral
D1-D5. Conseguenza scientifica: `pnl_net_fee_funding_usd` sistematicamente sovrastimato (funding
mancante), il che inquinerebbe il dataset di M7.

## Decisione

Implementare il **funding ledger** come job APScheduler 8h nel **context-orchestrator**:

- **`HLPublicInfoClient.user_funding_history(user, start_time_ms, end_time_ms=None)`**
  (`src/aiat/context/collectors/onchain.py`): POST read-only `{"type":"userFunding",...}` al `/info`
  pubblico HL. Nessuna private key (l'indirizzo wallet è pubblico). Rispecchia
  `Info.user_funding_history` dell'SDK.
- **`FundingReconciler`** (`src/aiat/orchestration/funding_reconciler.py`): apre una sessione DB,
  carica le posizioni OPEN dell'esperimento, mappa `model_id → wallet_address` dalla tabella
  `models`, interroga `userFunding` per wallet, e per ogni pagamento orario crea un `FundingEvent`
  **contro la posizione aperta** di quel `(model, coin)` che era già aperta al tempo del funding.
- **Leggere i pagamenti reali, non ricalcolarli**: `funding_amount_usd`/`funding_rate` vengono dal
  delta USDC e dal rate che HL ha effettivamente applicato (venue-accurate), non da `notional × rate`.
- **Idempotenza per chiave naturale `(position_id, funding_period_end)`** via *check-then-insert*.
  NON viene aggiunta una UNIQUE constraint (niente migration): il job è single-instance
  (`max_instances=1`), quindi non c'è race di scrittura. Lookback di 25h per sovrapporre le finestre
  di run consecutivi (i duplicati vengono deduplicati) e tollerare un run mancato.
- **Wiring**: `build_scheduler_for_orchestrator` accetta un `funding_job` opzionale (CronTrigger
  `hour="0,8,16"`); `__main__._build_funding_job` lo costruisce. `outcomes.sum_funding_usd` già
  somma la tabella alla chiusura → nessun altro cablaggio.

`funding_period_end` = timestamp del pagamento HL; `funding_period_start` = `end − 1h` (funding orario,
soddisfa `chk_funding_period_end_gt_start`).

## Conseguenze

### Positive
- `funding_events` finalmente popolato → `pnl_net_fee_funding_usd` corretto per M7.
- Ledger venue-accurate (legge i pagamenti reali), non una stima.
- Idempotente e resiliente a run mancati/sovrapposti senza migration.
- Fetch read-only sul wallet pubblico: nessun segreto nell'orchestrator (least privilege intatto).

### Negative
- L'orchestrator ora legge la tabella `models` (wallet_address) e scrive `funding_events` per tutti
  i modelli: prima non toccava `models`. È una scrittura ledger cross-model (come i `context_snapshots`
  condivisi), non una query agent (inv #1 riguarda le query agent).
- `check-then-insert` fa una SELECT per record; volume M6.2 trascurabile.

### Neutre (trade-off accettati)
- **Shape della response `userFunding` è un'ASSUNZIONE** da validare su testnet live
  (`{"time", "delta": {"type":"funding","coin","usdc","fundingRate"}}`), come il pattern «ASSUMPTION
  (validate M4-T08)» già presente nel client. Il parser è difensivo (scarta record malformati/coin
  non supportate) e il test usa la shape reale documentata.
- Job nell'orchestrator (come da istruzione/PRD) invece che per-agent: l'orchestrator ha già
  session factory + client HL read-only; il funding è un fatto per-wallet, non market context (inv #13
  non è violato — quello riguarda il fetch di sorgenti esterne durante il tick di decisione).

## Alternative considerate

### Alternativa A: job per-agent (ogni agent riconcilia il proprio wallet)
- Pro: isolamento per modello naturale; l'agent ha già `RealHyperliquidClient`.
- Contro: il PRD colloca il job nell'orchestrator; duplicherebbe la logica in 4 processi; l'agent
  fetcherebbe una sorgente esterna fuori dal context_snapshot (attrito con lo spirito di inv #13).
- Scartata perché: il PRD §4.2 è esplicito («job parallelo» lato orchestrator) e l'orchestrator ha
  già gli strumenti.

### Alternativa B: UNIQUE `(position_id, funding_period_end)` + `ON CONFLICT DO NOTHING`
- Pro: idempotenza a livello DB, robusta anche a concorrenza.
- Contro: richiede una migration (constraint su tabella esistente).
- Scartata perché: il vincolo «migration solo se inevitabile» + job single-instance rendono il
  check-then-insert applicativo sufficiente e senza migration.

## Test gating

- `tests/e2e/test_funding_reconciler.py`: contro Postgres reale, con fake funding source che ritorna
  la shape reale HL → verifica creazione `FundingEvent` (importo/rate/periodo) e **idempotenza**
  (secondo run: 0 creati, skip). Tripwire: pre-fix non esisteva alcun writer → il test non passa.
- `tests/unit/orchestration/test_funding_reconciler.py`: parser puro (shape reale + casi di scarto).
- `tests/unit/context/test_onchain.py`: `user_funding_history` (payload, non-200, body non-lista).

## Propagazione

- [x] Implementato in `src/aiat/orchestration/funding_reconciler.py` + `HLPublicInfoClient.user_funding_history`
- [x] Wiring scheduler (`build_scheduler_for_orchestrator(funding_job=...)`) + `__main__._build_funding_job`
- [x] Test e2e + unit
- [ ] Validare la shape reale di `userFunding` contro testnet live (M7 step di verifica)
- [ ] Aggiornare `PRD_V2.md` §4.2 con riferimento a questo ADR
