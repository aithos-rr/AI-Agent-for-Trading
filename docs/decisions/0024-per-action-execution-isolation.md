# ADR-0024: Isolamento errori esecuzione per-azione + tassonomia execution_status

**Data**: 2026-06-29
**Status**: accepted (ratificato da Riccardo, 2026-06-29)
**Milestone**: M5-T14 (scoperta empirica), vincolante per M7 (esecuzione + analisi outcome)
**PRD reference**: §4.1 step [8]/[10]; §3.2.3 (`decision_actions`); §7.6 (DecisionsRepository);
ADR-0015 (size_units), ADR-0022 (smoke M5-T14), ADR-0023 (provider-aware/latency)
**Closes deferral**: none

## Contesto

M5-T14 (smoke con LLM reali su HL testnet, ADR-0022) ha fatto girare **Opus 4.8**
(`usa-premium`/anthropic) per un tick reale: il modello ha aperto **2 posizioni reali**
(BTC LONG, ETH LONG) + 6 ordini con `hl_order_id` reali, entry **filled**, tenendo SOL a
HOLD. Due difetti sono emersi solo contro il percorso reale (il mock li nascondeva):

1. **Bookkeeping mancante.** `decision_actions.execution_status` restava al server-default
   `'pending'` e `executed=false` **nonostante l'esecuzione fosse avvenuta**:
   `_execute_actions` chiamava `execute_action` + `open_position`/`close_position` ma **non
   scriveva mai** lo stato dell'azione. I dati di tesi risultavano falsati (un FILLED reale
   indistinguibile da un `pending` mai eseguito).
2. **Niente isolamento errori.** Una singola `ExecutionRejectedError`/`ExecutionTimeoutError`
   (margin, size limit, timeout) **propagava** → `_execute_tick` rilanciava → `run_once`
   marcava l'intero run `FAILED`. Un ordine rifiutato su un simbolo **perdeva anche gli
   altri due** simboli del tick (all-or-nothing).

## Decisione

### 1. Metodo repo dedicato (no SQL inline)
Nuovo `DecisionsRepository.mark_action_execution(action_id, *, status, executed, error=None)`.
Il bounded context **decision** (§7.6) possiede `decision_actions`; il decision loop lo invoca
dopo ogni tentativo. `status` è tipizzato sull'enum `ExecutionStatus` (no stringhe sparse).

### 2. Tassonomia execution_status
Mappata sull'enum `ExecutionStatus` e sul CHECK `chk_action_execution_status`, **già allineati**
→ **nessuna migrazione**.

| Caso | execution_status | executed |
|------|------------------|----------|
| HOLD (mai tocca l'exchange) | `not_applicable` | false |
| FLAT senza posizione aperta / LONG·SHORT same-side (no add-to-position in v2) | `not_applicable` | false |
| entry/close **filled** | `filled` | true |
| entry **partial** | `partial` | true |
| rejected / timeout | `failed` | false (+ `execution_error`) |

L'esito traccia l'**ordine primario**: la `ENTRY` quando si apre (un flip opposite-side emette
anche una `CLOSE`, ma l'intento dell'azione è la nuova entry), altrimenti la `CLOSE` per un FLAT
puro. I trigger protettivi SL/TP (`triggered`) **non** sono primari.

### 3. Isolamento errori per-azione
`try/except` attorno a `execute_action` che cattura **solo** `(ExecutionRejectedError,
ExecutionTimeoutError)` → marca **quella** azione `failed` (+ `execution_error`), logga
(`action_execution_failed`), **prosegue** con le altre. Gli errori DB (es. `IntegrityError` da
`open_position`) **non** sono catturati → propagano e abortiscono il tick: una posizione che
esiste sull'exchange ma non in DB è un'inconsistenza che **deve** fallire forte.

### 4. Stato del run
`_execute_actions` ritorna il **conteggio** delle azioni fallite. Step [10]:

- 0 fallimenti → `SUCCESS` (invariato).
- ≥1 fallimento (a tick completato) → `PARTIAL`.

`FAILED` resta **riservato** all'abort-da-eccezione (suo significato attuale, settato da
`_finalize_run`). Anche "**tutte** le azioni non-HOLD fallite" entro un tick completato è
`PARTIAL`, **non** `FAILED`: la pipeline ha comunque completato (decisione persistita, closure
controllate, run finalizzato) e il dettaglio per-azione vive già su
`decision_actions.execution_status`; l'analisi outcome filtra su `executed=True`.

### 5. Mark dopo la persistenza
L'azione è marcata `filled` **solo dopo** che `open_position`/`close_position` sono andate a
buon fine. Se la persistenza rompe, l'azione resta `pending` e il tick → `FAILED` (l'ordine ha
toccato l'exchange ma il DB non lo conferma: incoerenza da investigare, non da nascondere).

## Conseguenze

### Positive
- **Robustezza M7**: un ordine rifiutato su un simbolo non perde gli altri simboli del tick.
- **Dati di tesi affidabili**: `execution_status` per-azione riflette la realtà exchange;
  l'analisi outcome può filtrare `executed=True`; FILLED / NOT_APPLICABLE / FAILED ora sono
  distinti esplicitamente (basta il `pending` fantasma).
- **Audit fedele del run**: l'evento di log `decision_loop_success` (nome mantenuto per
  continuità dei log/dashboard) **non implica più** che ogni ordine sia stato eseguito — i
  campi `status` e `failed_actions` dell'evento portano la verità.

### Negative / Limiti
- **Granularità della causa**: `execution_error` è testo troncato a 1000 char, non una
  tassonomia strutturata dei motivi (margin vs size vs auth). Sufficiente per l'audit; una
  classificazione fine è fuori scope.
- **`partial` predisposto ma inattivo**: i client non producono ancora un fill parziale (il
  client reale **rilancia** se l'entry non è `filled`). La mappatura `PARTIAL` è pronta ma
  resterà inattiva finché un fill parziale reale non emergerà (da osservare in M5-T14/M7).
- **Flip parziale (close ok + entry fallita) — limite noto, follow-up**: in un flip
  opposite-side, `RealHyperliquidClient.execute_action` esegue `close` poi `_open_orders` e
  ritorna `[close, *entry...]`. Se `_open_orders` solleva **dopo** che il close è andato a
  buon fine sull'exchange, l'eccezione propaga prima del `return`: l'azione viene marcata
  `FAILED`/`executed=False` e il close **non** viene persistito in quel tick. **Impatto
  contenuto**: (a) è in gran parte **pre-esistente** — prima di questo ADR la stessa eccezione
  abortiva l'intero tick e il close non veniva comunque persistito; (b) è **auto-sanante**:
  `_check_pending_closures` al tick successivo rileva la chiusura on-exchange (per symbol) e
  chiama `close_position`. Resta impreciso solo lo `execution_status` dell'azione (FAILED
  mentre il close è avvenuto). Il fix corretto tocca la **semantica di atomicità due-fasi del
  flip nel `RealHyperliquidClient`** (non il decision loop) → decisione di design separata,
  da aprire con ADR dedicato e sign-off; **fuori dallo scope di ADR-0024**.

## Test gating

- `tests/unit/orchestration/test_decision_loop.py::TestExecuteActionsStateTransition`:
  filled→`FILLED`+executed; HOLD→`NOT_APPLICABLE` senza toccare l'exchange; azione rifiutata
  → quella `FAILED`+error mentre le **altre** eseguono; `ExecutionTimeoutError` isolata come la
  rejected; e2e `run_once` → run `PARTIAL`.
- `tests/unit/orchestration/test_decision_loop.py::TestActionExecutionOutcome`: mapping diretto
  dell'esito — filled/partial/close-only→`FILLED`/`PARTIAL`; flip CLOSE+ENTRY → ENTRY primario;
  solo trigger SL/TP o lista vuota → `NOT_APPLICABLE`.
- `tests/integration/test_db_repositories_decisions.py`: default `pending`; mark `filled`,
  `not_applicable`, `partial`; mark `failed` con troncamento a 1000 char; `action_id`
  inesistente → `ValueError`.

## Propagazione

- [x] `src/aiat/db/repositories/decisions.py`: `mark_action_execution` + `_EXECUTION_ERROR_MAXLEN`
- [x] `src/aiat/orchestration/decision_loop.py`: `_execute_actions` ritorna il failed-count,
      marca ogni azione, `try/except` per-azione; helper `_action_execution_outcome`;
      step [10] sceglie `PARTIAL`/`SUCCESS`
- [x] Test unit (4) + integration (4)
- [x] Indicizzato in `docs/decisions/README.md`
- [x] Nessuna migrazione (CHECK `chk_action_execution_status` già allineato all'enum)
