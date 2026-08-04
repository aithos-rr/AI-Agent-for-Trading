# ADR-0039: Ogni lookup di posizioni aperte è scoped all'experiment corrente (fix root-cause cross-experiment leakage)

**Data**: 2026-08-04
**Status**: accepted
**Milestone**: M6.2 (pre-M7)
**PRD reference**: §4.1 (tick loop, path FLAT), §4.4 (job per-tick orchestrator), §7.6 (bounded context positions), §5 inv #1/#12
**Closes deferral**: none

## Contesto

Il gate M6.2 è rosso: sul dump di produzione del 2026-08-04 il dataset dello smoke risulta
sistematicamente corrotto sulle chiusure. La causa è **una sola query senza filtro**.

`PositionsRepository.list_open_for_model(model_id)` selezionava le posizioni aperte con

```sql
WHERE model_id = :model_id AND closed_at IS NULL
```

**senza** `experiment_id`. Il metodo è il punto di lettura condiviso da tre consumatori: il
bookkeeping del FLAT (`decision_loop._execute_actions`), la detection chain↔DB
(`decision_loop._reconcile_chain_state`, ADR-0025) e il `ClosureReconciler` (ADR-0038). Per il DB
"aperta" è uno stato **globale**, non relativo a un esperimento: le righe lasciate aperte da M6.1
(experiment `5555…`, policy *annotate not repair* di ADR-0035) erano quindi perfettamente visibili
allo smoke (experiment `6666…`), che gira sugli **stessi** `model_id` e sugli **stessi** wallet.

### Prova

Posizione `f2207b7b-bf25-4cb9-89f7-959a238f35f1` — `cn-cheap`, ETH, experiment `5555…` (M6.1),
aperta il 2026-07-27. Risulta chiusa il **2026-07-29 18:30 UTC** da una `decision_action`
dell'experiment **`6666…`** (smoke), con `exit_price = 1909.60` (il prezzo ETH dello smoke, non un
prezzo del 27/07) e `realized_pnl = -4.60` — un PnL calcolato su **entry vecchia × exit nuova**.
Nessuno dei due esperimenti descrive quella riga.

### Il meccanismo a cascata

Il danno non si ferma alla prima riga: la coda delle chiusure si sposta **permanentemente** di uno.

1. **Shift FIFO.** Il FLAT prende la prima riga aperta per `(model_id, symbol)`; con la riga
   archiviata M6.1 in testa, chiude **quella** invece della posizione che il modello ha appena
   chiuso on-chain, e le scrive addosso `exit_price`/`fee`/`closed_at` dell'evento **corrente**.
   La riga corretta resta aperta e diventa la nuova testa della coda: al FLAT successivo tocca a
   lei, con i dati di *quel* FLAT. Da lì in avanti **ogni** chiusura è sfasata di una posizione —
   ~**147 righe `model_close`** contaminate (exit/fee/`closed_at`/`closing_action_id` appartenenti
   a un evento diverso da quello della riga).
2. **7 zombie permanenti.** Con la coda sfasata resta sempre **una** riga orfana in testa per ogni
   `(model, symbol)` attivo: 7 righe aperte in DB e chiuse on-chain. Non sono recuperabili dal
   `ClosureReconciler`: il FLAT ha **chiuso la posizione sul venue**, e Hyperliquid alla chiusura
   **cancella i trigger** SL/TP associati. Gli `oid` di quei trigger non compariranno mai in
   `user_fills`, quindi il match per-oid di ADR-0038 non può scattare — per costruzione, non per
   bug.
3. **Detection avvelenata.** Lo stesso read alimenta `detect_chain_divergences`: le **8** righe
   M6.1 ancora aperte venivano confrontate con lo stato chain del wallet dello smoke e generavano
   `ChainDivergence` (`zombie_row`) **dal tick 1**, rendendo l'alert inutilizzabile come segnale.

Il `ClosureReconciler` (ADR-0038) **funziona**: nello smoke ha bookkeppato correttamente 5 chiusure
autonome. Il bug è a **monte**, nello scoping delle query — la sua `_models_with_open_positions` era
già filtrata per experiment, ma `_close_for_model` ri-leggeva poi con il metodo non filtrato, quindi
bastava che un modello avesse **una** riga aperta nell'experiment corrente (che è ciò che lo rende
visitato) per trascinarsi dentro anche le righe degli esperimenti archiviati.

### Censimento (tutte le letture di posizioni aperte)

| Call-site | Filtro pre-fix | Esito |
|-----------|----------------|-------|
| `db/repositories/positions.py::list_open_for_model` | `model_id` + `closed_at IS NULL` | **BUG** — origine, corretto |
| `orchestration/decision_loop.py::_execute_actions` (path FLAT) | via `list_open_for_model` | **BUG** — corretto (passa `settings.experiment_id`) |
| `orchestration/decision_loop.py::_reconcile_chain_state` (detection ADR-0025) | via `list_open_for_model` | **BUG** — corretto (passa `settings.experiment_id`) |
| `orchestration/closure_reconciler.py::_close_for_model` | via `list_open_for_model` | **BUG** — corretto (passa `self._experiment_id`) |
| `orchestration/closure_reconciler.py::_models_with_open_positions` | `experiment_id` + `closed_at IS NULL` | già corretto |
| `orchestration/funding_reconciler.py::_open_positions` | `experiment_id` + `closed_at IS NULL` | già corretto |
| `scripts/repair_zombie_positions.py` (one-shot ADR-0035) | `experiment_id` + `model_id` + `symbol` | già corretto, **non toccato** |
| `context/builder.py`, `execution/guardrails.py` | — | nessuna query su `positions` (il `PortfolioState` viene dalla chain, non dal DB) |
| dashboard | — | fuori repo (artefatto di deploy separato); nessuna query su `positions` nel monorepo |

`close_position(position_id, …)` e `open_position(action_id, …)` **non** sono lookup di posizioni
aperte: la prima carica per PK, la seconda deriva l'experiment dalla `decision_action`. Lo scoping
corretto sta a monte, in chi sceglie *quale* posizione chiudere.

## Decisione

**Lo scoping vive nel repository, non nei chiamanti.** La firma diventa:

```python
async def list_open_for_model(self, *, experiment_id: str, model_id: str) -> list[Position]:
```

con `experiment_id` **obbligatorio e keyword-only** (non un parametro opzionale con default, non un
`filter_experiment: bool`). Le tre proprietà che volevamo:

1. **Non si può dimenticare il filtro.** Un chiamante non ha una variante non scoped da invocare per
   sbaglio; l'unico errore possibile è passare il valore *sbagliato*, che è un errore visibile.
2. **La vecchia forma di chiamata non compila.** `list_open_for_model(model_id)` (posizionale) è un
   `TypeError` a runtime e un errore mypy in CI: la migrazione dei call-site è forzata, non
   affidata a una grep. Keyword-only anche per evitare lo scambio silenzioso di due argomenti
   entrambi `str`.
3. **Un solo punto da rivedere** quando cambieranno le regole di visibilità.

Aggiunto anche un `ORDER BY opened_at ASC, id ASC` esplicito: il path FLAT prende la **prima** riga
che matcha il simbolo, e prima quell'ordine era l'heap order di Postgres (non deterministico per
contratto). Ora il comportamento — "chiude la più vecchia tra quelle aperte dell'experiment
corrente" — è dichiarato e testabile invece che accidentale. La selezione tra più righe aperte
**dentro lo stesso experiment** resta invariata (FIFO): non è lo scenario di questo ADR, ed è
coperta a monte dal `ClosureReconciler` che gira al secondo 0 di ogni tick (ADR-0038).

Nessuna migration, nessun cambio DDL: è un fix di query e di firma.

## Conseguenze

### Positive

- **"Annotate not repair" diventa permanentemente sicura.** Con lo scoping, le righe aperte di un
  esperimento archiviato sono invisibili **per costruzione** a FLAT, detection, reconciler e
  context. Questa è la conseguenza voluta, e va letta come tale: la decisione di ADR-0035 (lasciare
  aperte le righe M6.1 con annotazione, senza riparare) non è più una fonte latente di
  contaminazione per gli esperimenti successivi. Le 8 righe M6.1 e le 7 zombie dello smoke possono
  restare dove sono.
- La detection `ChainDivergence` torna a essere un segnale utile: confronta lo stato chain del
  wallet con le righe del **solo** experiment in corso.
- Il `ClosureReconciler` non prova più a bookkeppare righe i cui trigger sono stati cancellati anni
  luce fa: risparmia lavoro e non produce falsi `still_open`.
- Rinforza l'invariante #1 (isolation) sull'asse experiment, non solo su quello `model_id`.

### Negative

- Un wallet HL è condiviso tra esperimenti successivi (stesso `model_id`, stesso indirizzo): lo
  scoping rende il DB coerente ma **non** riconcilia la chain, dove la posizione di un esperimento
  archiviato — se davvero ancora aperta — continuerebbe a esistere. Per M6.1 non è il caso (tutte
  chiuse on-chain), ma resta una precondizione operativa: **un nuovo esperimento parte da wallet
  flat**.
- Le righe contaminate dello smoke **non** vengono riparate. È una scelta esplicita: dataset
  throwaway, il re-smoke girerà su un `experiment_id` nuovo. Nessuno script di repair, nessun
  intervento sui dati.

### Neutre (trade-off accettati)

- `close_position` resta senza filtro experiment (carica per PK): un `position_id` sbagliato passato
  a mano resta possibile. Difenderlo richiederebbe di duplicare lo scoping su una API che non
  seleziona nulla.
- L'unicità "al più una posizione aperta per `(experiment, model, symbol)`" resta **non** imposta
  dal DDL (esiste solo l'indice parziale su `closed_at IS NULL`). Un CHECK/unique index sarebbe
  un'ulteriore rete, ma è un cambio DDL fuori dallo scope di questo fix.

## Alternative considerate

### Alternativa A: filtrare nei chiamanti, lasciando il repository invariato

- Pro: diff minimo, nessuna firma da cambiare.
- Contro: è esattamente il modo in cui il bug è nato — `_models_with_open_positions` filtrava,
  `_close_for_model` no. Il quarto chiamante che arriverà tra sei mesi non ha nessun motivo
  strutturale per ricordarsene.
- **Scartata**: sposta la responsabilità dove non è verificabile.

### Alternativa B: `experiment_id: str | None = None` (filtro opzionale)

- Pro: retro-compatibile, nessun call-site da toccare.
- Contro: il default sarebbe il comportamento **buggato**, e il codice esistente resterebbe rotto in
  silenzio. Un parametro opzionale non è un invariante.
- **Scartata**.

### Alternativa C: riparare le righe contaminate dello smoke (repair script stile ADR-0035)

- Pro: dataset smoke consistente.
- Contro: lo smoke è un dataset **throwaway** — serviva a validare la pipeline, non a produrre
  risultati. Ricostruire l'accoppiamento corretto riga↔evento su ~147 chiusure sfasate richiede di
  invertire uno shift a partire dai `user_fills`, con rischio di introdurre errori nuovi in dati che
  verranno buttati. Il re-smoke gira su un experiment nuovo.
- **Scartata**: costo e rischio senza beneficio.

### Alternativa D: dare a ogni esperimento wallet HL distinti

- Pro: risolverebbe anche il lato chain (il residuo dichiarato sopra).
- Contro: fuori scope, con implicazioni su seed/funding/costi; e non risolverebbe comunque il
  problema *nel DB*, dove le query resterebbero non filtrate.
- **Scartata come sostituto**; resta valida come irrigidimento futuro.

## Test gating

Tutti mutation-proof: rimuovendo il predicato `Position.experiment_id` da `list_open_for_model`
(lasciando la firma nuova, così il test fallisce sul **comportamento** e non sulla signature) i
quattro test qui sotto diventano rossi — verificato.

- `tests/e2e/test_cross_experiment_scoping.py::test_flat_closes_current_experiment_row_not_the_archived_one`
  — **(a)** lo scenario 29/07 18:30 riprodotto: due righe aperte per lo stesso `(model, symbol)`,
  una nell'experiment archiviato e una in quello corrente; un FLAT su ETH deve chiudere la riga
  **corrente** (exit 1909.60, `close_reason=model_close`, `closing_action_id` = la FLAT action) e
  lasciare quella archiviata **intatta** (`closed_at`/`exit_price`/`realized_pnl`/`close_reason`
  NULL). Un solo `Outcome`, e appartiene alla riga corrente.
- `tests/e2e/test_cross_experiment_scoping.py::test_detection_ignores_archived_experiment_rows`
  — **(b)** riga aperta di un experiment archiviato + chain flat → **zero** righe `errors` per
  l'experiment corrente. Accoppiato al controllo positivo
  `test_detection_still_flags_current_experiment_zombie` (stessa forma **dentro** l'experiment
  corrente → esattamente un `zombie_row`), così "non ha trovato niente" non può passare a vuoto.
- `tests/integration/test_closure_reconciler.py::test_archived_experiment_positions_are_never_booked`
  — **(c)** il reconciler visita il modello (ha una posizione aperta nell'experiment corrente, il
  cui trigger **non** è scattato) ma non carica né bookkeppa la riga archiviata, nemmeno con il suo
  `oid` di trigger presente nei `user_fills`. Verifica anche che la finestra `user_fills` sia
  ancorata alla posizione più vecchia **dell'experiment corrente**.
- `tests/integration/test_db_repositories_positions.py::test_list_open_for_model_excludes_other_experiments`
  — il contratto del repository, simmetrico nei due sensi (ogni experiment vede solo le proprie
  righe: il filtro è un predicato reale, non un "nascondi tutto tranne l'ultima").
- **(d) Regressione**: i 5 scenari ADR-0038 (`tests/integration/test_closure_reconciler.py`) e i 3
  scenari ADR-0025 (`tests/e2e/test_chain_reconciliation_e2e.py`) restano verdi, prima e dopo il
  fix.
- `tests/e2e/test_isolation.py::test_list_open_positions_excludes_other_model` (inv #1) aggiornato:
  le posizioni del seed vivono in un experiment dedicato, ora passato esplicitamente alla query.

## Propagazione

- [x] `src/aiat/db/repositories/positions.py::list_open_for_model` — `experiment_id` obbligatorio
      keyword-only + `ORDER BY opened_at, id`
- [x] Call-site corretti: `decision_loop._execute_actions`, `decision_loop._reconcile_chain_state`,
      `closure_reconciler._close_for_model`
- [x] Test (a)/(b)/(c) + contratto repository, tutti mutation-proof
- [x] Indice ADR aggiornato (`docs/decisions/README.md`)
- [x] Nessuna migration (nessun cambio DDL)
- [x] Nessun intervento sui dati: righe M6.1 e zombie dello smoke lasciate come sono
      (*annotate not repair*, ora sicura per costruzione)
- [ ] Re-smoke M6.2 su un `AIAT_EXPERIMENT_ID` **nuovo**, con wallet flat all'avvio
