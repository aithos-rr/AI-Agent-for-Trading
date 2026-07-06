# ADR-0027: Gap di bookkeeping sul path di chiusura FLAT (order `close` mancante, `closing_action_id` NULL, CHECK bucato)

**Data**: 2026-07-01
**Status**: accepted — **Fix implementato: 2026-07-06 (commit 1ec8029)**
**Milestone**: M5-T14 (scoperto in review avversariale), follow-up **pre-M6 BLOCCANTE** (chiuso 2026-07-06)
**PRD reference**: §3.2.3 (`decision_actions`); §7.6 (DecisionsRepository / PositionsRepository);
§4.1 step [8]/[10]; ADR-0024 (execution bookkeeping per-azione), ADR-0025 (atomicità flip)
**Closes deferral**: none (tracciamento di un difetto; il fix è una sessione dedicata pre-M6)

## Contesto

M5-T14 (smoke con LLM reali su HL testnet, ADR-0022) ha fatto girare l'**Agent OpenAI**
(`usa-cheap`, `gpt-4.1-mini`) su tick reali. Al **tick-2**, una decisione **FLAT su SOL** ha
**chiuso la posizione on-chain**: confermato via `clearinghouseState` (SOL **assente** da
`assetPositions`; realized PnL **+0.201**). La chiusura è dunque **realmente avvenuta**
sull'exchange e lo stato posizione DB↔chain è coerente.

Tuttavia il path di chiusura FLAT **persiste la posizione in modo parziale**: la chiusura è
registrata (`closed_at`, `exit_price`, `realized_pnl_usd`, `close_reason`) ma il bookkeeping è
incompleto su tre punti — stessa famiglia di ADR-0024 (esecuzione avvenuta, bookkeeping
incompleto), ma sul path di **chiusura FLAT**, non di apertura.

1. **Nessuna riga `orders` con `order_kind='close'`.** Il CHECK `chk_order_kind` prevede
   **esplicitamente** il valore `'close'` (`entry|stop_loss|take_profit|close`), ma nel path FLAT
   **nessuna riga close viene inserita**. Il dataset `orders` è quindi **incompleto sulle
   chiusure**: le chiusure sono invisibili per l'audit di esecuzione, il calcolo dello slippage e
   il conteggio operazioni.

2. **`positions.closing_action_id` resta NULL** su una posizione chiusa → rompe la
   **tracciabilità decisione→chiusura**: non si risale dalla chiusura alla `decision_action` FLAT
   che l'ha causata.

3. **`chk_position_closed_consistency` ha un buco.** Il ramo "chiuso" verifica `closed_at`,
   `exit_price`, `realized_pnl_usd`, `close_reason` ma **NON** `closing_action_id`. Definizione
   attuale:

   ```sql
   CHECK (
     ((closed_at IS NULL) AND (exit_price IS NULL) AND (realized_pnl_usd IS NULL)
       AND (close_reason IS NULL) AND (closing_action_id IS NULL))
     OR
     ((closed_at IS NOT NULL) AND (exit_price IS NOT NULL) AND (realized_pnl_usd IS NOT NULL)
       AND (close_reason IS NOT NULL))
   )
   ```

   Ecco perché **SOL è passata con `closing_action_id` NULL**: il ramo chiuso non lo richiede, il
   difetto (2) non viene intercettato dal constraint.

### Relazione con altri ADR

- **ADR-0024**: stessa famiglia — l'esecuzione avviene ma il bookkeeping resta incompleto. Lì era
  il path di **apertura** (`execution_status` fantasma su `pending`); qui è il path di
  **chiusura FLAT**.
- **ADR-0025** (atomicità flip): imparentato — entrambi riguardano **modifiche a posizioni
  esistenti** (close/flip), non nuove aperture pulite.

## Decisione

Il fix era pianificato per una **sessione dedicata pre-M6**. **È stato implementato il
2026-07-06** (commit `1ec8029`) — vedi la sezione «Implementazione (2026-07-06)» in fondo.
Questo ADR **traccia** il difetto e ne fissa lo scope; la diagnosi qui sotto resta la storia
del difetto **come osservato al 2026-07-01**, non riscritta a posteriori.

Interventi previsti dal fix (ora implementati — dettaglio in «Implementazione (2026-07-06)»):

- **(a)** Inserire nel path FLAT una riga `orders` con `order_kind='close'` e `hl_order_id` reale,
  così le chiusure entrano nel dataset di audit al pari di entry/SL/TP.
- **(b)** Popolare `positions.closing_action_id` con la `decision_action` FLAT che ha causato la
  chiusura, ripristinando la tracciabilità decisione→chiusura.
- **(c)** Correggere `chk_position_closed_consistency` aggiungendo `closing_action_id IS NOT NULL`
  al ramo "chiuso" (**richiede migration** — mai modifica retroattiva del DDL).

## Conseguenze

### Impatto
- **NON blocca** la validazione-workflow di M5-T14: lo stato-posizione DB↔chain è coerente e il
  PnL è registrato (`realized_pnl_usd = +0.201` per SOL).
- **BLOCCA M6**: il **dataset è il risultato scientifico** dell'esperimento. Senza le righe
  `orders` di chiusura e senza `closing_action_id`, l'audit esecuzione/slippage e la tracciabilità
  decisione→chiusura sono incompleti — inaccettabile per lo smoke 48h e l'analisi.

### Limiti / Note
- Finché il fix (c) non è applicato, il constraint **non** protegge dall'inserimento di posizioni
  chiuse con `closing_action_id` NULL: è una difesa mancante, non solo un dato mancante.
- Il fix (a) va coordinato con il path di chiusura da SL/TP e da flip (ADR-0025) per evitare
  doppie righe `orders` o `close` duplicati sullo stesso evento exchange.

## Test gating (per la sessione di fix, non per questo ADR)

- Path FLAT: una chiusura reale/mock inserisce **esattamente una** riga `orders`
  `order_kind='close'` con `hl_order_id` e popola `positions.closing_action_id`.
- Migration: `alembic upgrade head` + `alembic downgrade base` su Postgres pulito; il nuovo
  `chk_position_closed_consistency` **rifiuta** una posizione chiusa con `closing_action_id` NULL.
- Regressione: le chiusure da SL/TP e da flip continuano a produrre bookkeeping coerente.

## Propagazione

- [x] Difetto tracciato in questo ADR (scoperto in review avversariale M5-T14)
- [x] Indicizzato in `docs/decisions/README.md`
- [x] Sessione fix pre-M6: (a) order `close`, (b) `closing_action_id`, (c) migration del CHECK (migration 004) — 2026-07-06
- [ ] Coordinamento con ADR-0025 (flip) sul path di modifica posizioni esistenti
- [ ] (PRD/schema non modificati da questo ADR: solo tracciamento)

## Implementazione (2026-07-06)

Fix implementato e committato (commit `1ec8029`, sessione 2026-07-06). Tutti i test verdi
(**680 passed**, a parte una failure preesistente e non correlata in `test_settings`).

- **fix (a) — riga `orders` `order_kind='close'` + fee**: `close_position`
  (`src/aiat/db/repositories/positions.py`) ora inserisce una riga `Order` con
  `order_kind=close_order.order_kind.value` (`'close'`) e `hl_order_id` reale, replicando il
  pattern del path di apertura; se `close_order.fee_usd` è valorizzato crea anche il `FeeEvent`
  di chiusura **prima** del `select sum(fee_usd)`, così la fee di chiusura entra nel PnL netto.
- **fix (b) — `closing_action_id` popolato**: `close_position` setta
  `pos.closing_action_id = uuid.UUID(closing_action_id)` (nuovi parametri `closing_action_id` +
  `close_order` sulla firma); il chiamante `decision_loop.py` passa `str(db_action.id)` e il
  `close_order` dell'azione FLAT (`src/aiat/orchestration/decision_loop.py`).
- **fix (c) — migration del CHECK**:
  `alembic/versions/004_fix_position_closed_consistency_check.py` droppa e ricrea
  `chk_position_closed_consistency` aggiungendo `closing_action_id IS NOT NULL` al ramo «chiuso»
  (downgrade ripristina il CHECK vecchio; `down_revision="003"`). Allineato anche
  `__table_args__` del model `Position` (`src/aiat/db/models/position.py`) per coerenza col DDL.
- **test**:
  - gating `test_close_position_persists_close_order_and_action_id` — verifica esattamente una
    riga `orders` `order_kind='close'` con `hl_order_id`, `closing_action_id` popolato (≠ opening
    action), e la fee di chiusura inclusa in `sum_fees_usd`/PnL netto;
  - teeth-test `test_close_position_consistency_check_requires_closing_action_id` — verifica che
    il CHECK post-004 **rifiuti** (`IntegrityError`) una posizione chiusa con `closing_action_id`
    NULL e gli altri campi di chiusura valorizzati (caso discriminante che sotto il CHECK
    pre-004 sarebbe passato).
  - Entrambi in `tests/integration/test_db_repositories_positions.py`.
