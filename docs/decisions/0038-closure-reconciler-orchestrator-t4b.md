# ADR-0038: Bookkeeping delle chiusure autonome SL/TP a livello orchestrator (fix root-cause T4b)

**Data**: 2026-07-27
**Status**: accepted
**Milestone**: M6.2 (pre-M7)
**PRD reference**: §4.1 (tick loop, ex step 9), §4.4 (orchestrator per-tick jobs), §5 inv #12/#13
**Closes deferral**: none

## Contesto

Le chiusure **autonome** di una posizione (lo stop-loss o il take-profit scatta sul venue senza una
decisione del modello) devono essere bookkept in DB: `positions.closed_at`/`exit_price`/`close_reason`,
un `Outcome`, e la `taker_close` fee (ADR-0032). Finora questo lavoro viveva **dentro** il tick
dell'agente — `decision_loop._check_pending_closures`, PRD §4.1 step 9. Aveva **due** modalità di
fallimento che lasciavano posizioni **"zombie"** (aperte in DB, chiuse on-chain). Documentate: **5
occorrenze in 20 giorni**, riparate una-tantum da ADR-0035.

- **M1 — ordine degli step + short-circuit di simbolo.** `_check_pending_closures` girava **DOPO**
  `_execute_actions` e la detection (`check_position_closure`) short-circuitava su `szi != 0` a
  livello **SIMBOLO**. Se uno SL/TP scattava tra due tick e nello stesso tick il modello **riapriva
  lo stesso simbolo**, la chain restava non-flat per quel simbolo: la chiusura precedente non veniva
  **mai** rilevata (il simbolo "risultava ancora aperto", ma era una posizione NUOVA e diversa).
- **M2 — dipendenza dal run.** Il bookkeeping girava **solo** se il tick raggiungeva lo step 9. Se il
  run falliva prima (es. `LLMUnrecoverableError`/`LLMError` per credito API esaurito, ADR-0037), il
  closure check **non girava mai**, e la posizione restava zombie per tutta la durata del blackout del
  modello (esattamente lo scenario dell'agente `usa-premium` morto in ADR-0035).

La detection T4/ADR-0025/0034 (chain↔DB `ChainDivergence` a inizio tick) è solo un **alert**: segnala
la divergenza ma non la bookkeepa. Serviva eliminare **entrambe** le cause radice, non solo allertare.

## Decisione

Spostiamo il bookkeeping delle chiusure autonome dal tick dell'agente al **context-orchestrator**, come
nuovo passo per-tick accanto a `FundingReconciler` e allo step baseline (ADR-0036). Nuovo componente
`aiat.orchestration.closure_reconciler.ClosureReconciler`, cablato in `__main__._build_orchestrator_tick_job`
ed eseguito **per primo** in `_orchestrator_tick` (best-effort: un suo fallimento logga
`closure_step_failed` e non blocca la build del `context_snapshot` da cui dipendono gli agent, inv #13).

Questo risolve **entrambe** le modalità:

1. **Fix M2 (run-independence).** Gira per **ogni** modello con posizioni aperte a prescindere dal fatto
   che il run di quel modello sia riuscito, fallito, o non sia mai partito. Isolamento per-modello
   (una `user_fills` fallita o un errore su un wallet non abortisce il batch — `try/except` per modello).
2. **Fix ordinamento di M1.** L'orchestrator fira al secondo 0 del tick; gli agent aprono nuove posizioni
   ~30s dopo (`agent_start_delay_seconds`). La chiusura del tick precedente è quindi bookkept **prima**
   che l'agente possa riaprire lo stesso simbolo nello stesso tick.
3. **Fix detection di M1 (per-posizione, non per-simbolo).** Nuova funzione pura
   `hyperliquid_client.detect_autonomous_closure(fills, trigger_oids)`: **nessuno** short-circuit su
   `szi`. Per **ogni** posizione aperta si prende l'`hl_order_id` dei suoi ordini trigger
   `stop_loss`/`take_profit` (`ClosureReconciler._trigger_oids`) e si cerca se **quel** oid ha fatto fill
   in `user_fills` (`fills[*].oid`). Un trigger che scatta conserva il proprio oid sul fill; le partial
   condividono un oid (somma per-oid riusata dal fix item-6, commit `8411576`). Una riapertura dello
   stesso simbolo è una posizione NUOVA con un ordine di apertura diverso, quindi non può mascherare la
   chiusura della vecchia.

Dettagli implementativi:

- **Detection pubblica, least-privilege.** L'orchestrator non ha private key: legge `user_fills_by_time`
  via `HLPublicInfoClient` (read pubblico per indirizzo, nessun wallet key). La finestra parte
  dall'`opened_at` più vecchia tra le posizioni aperte del modello meno `_WINDOW_BUFFER_MS` (60s, robusto
  a skew/latenza dei fill; l'idempotenza deduplica ogni overlap).
- **Riuso del path esistente, nessuna logica duplicata.** Il bookkeeping è `PositionsRepository.close_position`
  (ADR-0027/0030/0032): scrive outcome, `taker_close` fee_event legata all'ordine trigger scattato,
  e i campi di chiusura. L'attribuzione `close_reason` è l'euristica per-side **invariata** (ADR-0030),
  spostata as-is da `decision_loop` a `closure_reconciler._attribute_close_reason` (liquidazione vince;
  altrimenti LONG→SL se exit<entry altrimenti TP, SHORT invertito; exit==entry → SL + log anomalia).
- **`closing_run_id` (FK NOT NULL).** La chiusura è bookkept fuori da qualunque run del modello, ma
  `outcomes.closing_run_id` è NOT NULL. Convenzione (allineata alla convenzione 2 dello script di
  repair): si usa il run **più recente** del modello di **qualunque** status (`_latest_run_id`). Una
  posizione implica sempre un run di apertura, quindi esiste sempre; il guard `no_run` è irraggiungibile
  in pratica ma presente per non violare il vincolo.
- **Idempotenza per costruzione.** Si considerano solo posizioni `closed_at IS NULL`; una già chiusa non
  è mai ri-processata (nessun `Outcome`/fee duplicato), anche se gli stessi `user_fills` ricompaiono.
- **T4 resta rete di sicurezza.** La detection chain↔DB (ADR-0025/0034) rimane invariata; con questo
  passo attivo dovrebbe smettere di segnalare `ChainDivergence` in operatività normale.

`decision_loop`: rimossi lo step 9 (`_check_pending_closures`) e il modulo `_attribute_close_reason`
(spostato). Il path **model-close (FLAT)** resta nell'agent run (`_execute_actions`): quello è una
decisione del modello con un CLOSE `OrderResult` nostro, non una chiusura autonoma.

## Conseguenze

### Positive
- Elimina entrambe le cause radice delle zombie (M1 + M2), non solo l'allerta a posteriori.
- Detection per-posizione: corretta anche con reopen same-symbol e con più posizioni per simbolo.
- Run-independent: copre l'intero blackout di un modello morto (lo scenario di ADR-0035).
- Nessuna logica duplicata: riusa `close_position` + euristica ADR-0030; T4 resta safety net.

### Negative
- Il context-orchestrator ora fa una `user_fills` per modello con posizioni aperte per tick (read
  pubblico, cap `_HL_FILL_CAP=2000` con warning se la finestra è troncata). Costo I/O trascurabile.
- La `taker_close` fee di una chiusura autonoma è attribuita al run più recente, non al "run di
  chiusura" (che non esiste): scelta di convenzione, non un errore.

### Neutre (trade-off accettati)
- `check_position_closure` (il vecchio matcher per-simbolo sull'agent client) resta nel codice ma non è
  più chiamato in produzione: mantenerlo evita il churn su ~40 riferimenti negli e2e; i suoi test
  (incluso il regression item-6 sull'oid) restano verdi.
- La finestra parte dall'apertura più vecchia + buffer: su modelli con posizioni molto vecchie la
  finestra è ampia, ma l'idempotenza e il cap la rendono sicura.

## Alternative considerate

### Alternativa A: tenere il check nell'agent run ma spostarlo PRIMA di `_execute_actions` + `try/finally`
- Pro: modifica più piccola, nessun nuovo componente.
- Contro: risolve l'ordine di M1 ma **non** M2 nel caso peggiore (un `LLMError`/timeout prima ancora di
  raggiungere il check, o un run mai partito, lascia comunque la posizione zombie). Non elimina la
  dipendenza dal run.
- **Scartata**: non chiude M2 in modo robusto.

### Alternativa B: detection per-simbolo mantenendo lo short-circuit `szi`, ma girando a inizio tick
- Pro: riusa `check_position_closure` così com'è.
- Contro: la causa di M1 non è solo l'ordine, è lo short-circuit di **simbolo**: con un reopen same-tick
  il simbolo resta non-flat e la vecchia chiusura non è comunque rilevata. Serve detection per-oid.
- **Scartata**: non risolve la detection di M1.

### Alternativa C: bookkeepare direttamente dalla detection T4 (`ChainDivergence`)
- Pro: un solo path.
- Contro: T4 è un rilevatore di divergenza (alert), non ha l'attribuzione per-side né il match per-oid;
  trasformarlo in bookkeeper duplicherebbe la logica di `close_position`.
- **Scartata**: T4 resta come safety net indipendente.

## Test gating

- `tests/unit/execution/test_real_hyperliquid_client.py::TestDetectAutonomousClosure` — matcher puro:
  match per-oid, somma partial per-oid, VWAP exit, oid più recente tra SL/TP, flag liquidazione,
  exit non-positivo → None, nessun trigger oid → None.
- `tests/integration/test_closure_reconciler.py` — 4 scenari reali su Postgres effimero, fills canned
  (nessuna chiamata HL), testnet-only:
  - (a) SL scatta tra tick + reopen stesso simbolo stesso tick → chiude **solo** la vecchia posizione,
    la nuova resta aperta (**mutation-proof**: con detection per-simbolo fallisce);
  - (b) agente morto (unico run = `failed`) + SL durante il blackout → chiusa e attribuita al run
    fallito (**mutation-proof**: narrowing di `_latest_run_id` a `status='success'` fallisce);
  - (c) trigger non scattato → resta aperta, nessun outcome (no false positive);
  - (d) seconda passata → 0 chiusure, esattamente un outcome (idempotenza).
- Rimossi i 2 test in-run in `tests/unit/orchestration/test_decision_loop.py` (step 9 non esiste più);
  `TestAttributeCloseReason` migra col codice al reconciler.

## Propagazione

- [x] Implementato in `src/aiat/orchestration/closure_reconciler.py` +
      `src/aiat/execution/hyperliquid_client.py::detect_autonomous_closure`
- [x] Cablato in `src/aiat/__main__.py::_build_orchestrator_tick_job`
- [x] Rimosso lo step 9 in `src/aiat/orchestration/decision_loop.py`
- [x] Test unit (matcher) + integration (4 scenari, 2 mutation-proof)
- [x] Indice ADR aggiornato (`docs/decisions/README.md`)
- [ ] Aggiornare `PRD_V2.md` §4.1/§4.4 con riferimento a questo ADR (closure = job orchestrator, non step 9)
- [ ] Nessuna migration (nessun cambio DDL)
