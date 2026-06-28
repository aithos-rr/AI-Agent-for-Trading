# ADR-0016: Identità posizione = coin symbol (`positions.hl_position_id` vestigiale)

**Data**: 2026-06-28
**Status**: accepted
**Milestone**: M4 (ExecutionLayer) / M5 (decision_loop), vincolante per M4-T08
**PRD reference**: §4.1 (step 9), §7.5, §6.3 (`OpenPositionSummary`)
**Closes deferral**: none (correzione di un bug + ratifica di una convenzione implicita)

## Contesto

La review adversariale del lavoro di M4-T08 (implementazione di `RealHyperliquidClient`,
commit `d494457`) ha rilevato un **bug funzionale pre-esistente in M5**:

`orchestration/decision_loop.py::_check_pending_closures` (PRD §4.1 step 9, rilevazione
delle chiusure SL/TP scattate tra un tick e l'altro) iterava le posizioni aperte e faceva:

```python
for position in open_positions:
    if position.hl_position_id is None:
        continue                                  # ← SEMPRE vero
    closure_info = await self._hl_client.check_position_closure(position.hl_position_id)
    ...
```

Il campo `positions.hl_position_id` (`db/models/position.py`, nullable) **non viene mai
popolato** da nessuna parte del codice: `PositionsRepository.open_position` non lo scrive.
Di conseguenza la guardia `if position.hl_position_id is None: continue` **scartava sempre
ogni posizione**, e la rilevazione delle chiusure SL/TP (e quindi la scrittura delle
`outcomes` per stop-loss / take-profit) **non veniva mai eseguita**.

Il gate di M5 non ha catturato il difetto perché l'unico test che toccava il percorso
(`test_check_pending_closures_called`) mockava `PositionsRepository.list_open_for_model`
restituendo `[]` e asseriva solo che il metodo *fosse chiamato* — non esercitava mai il
ramo di rilevazione con una posizione reale (test shallow; cfr. caveat batching su M5).

### Radice concettuale

`hl_position_id` nasce dall'**assunzione errata** che Hyperliquid esponga un identificatore
stabile di posizione. Non è così: HL identifica le posizioni **per coin**. Il
`RealHyperliquidClient.check_position_closure` (introdotto in `d494457`,
`hyperliquid_client.py` righe ~661-662) interpreta infatti il parametro come **simbolo
della coin** e confronta contro `pos["coin"]` nello stato del wallet.

## Decisione

**L'identità operativa di una posizione è il suo `symbol` (coin).**

`decision_loop._check_pending_closures` passa `position.symbol` a `check_position_closure`,
e la guardia su `hl_position_id` è rimossa.

Giustificazione:

- **(a)** Hyperliquid non fornisce un position id stabile — identifica per coin.
- **(b)** Il design v2 garantisce **una sola posizione aperta per simbolo per wallet**
  (no add-to-position: same-side → ignora, opposite-side → close-then-open; confermato in
  PRD §7.5 e nel codice `hyperliquid_client.py` righe 85/148/497). Quindi il simbolo
  identifica univocamente la posizione aperta.
- **(c)** Gli schemi di dominio usano già `symbol: Literal["BTC","ETH","SOL"]` come chiave
  (`OpenPositionSummary`, `Position.symbol` non-null) e `idx_positions_model_symbol`.

Questa è la **stessa convenzione** già adottata da `RealHyperliquidClient` — l'ADR la
ratifica end-to-end e la usa per chiudere la divergenza fra loop e client.

### `positions.hl_position_id` → RIMOSSA

La colonna `positions.hl_position_id` (nullable, mai popolata con un id reale) era residuo
dell'assunzione errata. **RIMOSSA**: M4-T08 ha confermato sul campo l'identità = symbol
contro l'SDK reale (`check_position_closure` rileva le chiusure via symbol), quindi la colonna
non ha più ragion d'essere. Droppata nella **migrazione `003_drop_hl_position_id`**. Se un
venue futuro fornisse un id posizione stabile, riaggiungerla è una migrazione banale
(`add_column`, String nullable).

## Conseguenze

### Positive
- La rilevazione delle chiusure SL/TP (PRD §4.1 step 9) **funziona** end-to-end: loop e
  client concordano sull'identità = symbol.
- Coerenza fra `RealHyperliquidClient` (interpreta `hl_position_id` come symbol) e il loop
  (passa `position.symbol`).

### Negative / Note
- Colonna DB `hl_position_id` **rimossa** (migrazione `003`) dopo che M4-T08 ha confermato
  l'identità = symbol contro l'SDK reale. Costo della rimozione: una migrazione banale e
  reversibile; nessun dato perso (la colonna non era mai stata popolata).
- **Da validare su testnet reale (M4-T08)**: questa convenzione è legata all'**assunzione
  #2 del `RealHyperliquidClient`** (`hl_position_id` := coin symbol) e all'attribuzione di
  `close_reason` da `user_fills`. Entrambe vanno verificate contro le shape reali dei fill
  HL; se `check_position_closure` necessitasse di matchare oid di trigger per distinguere
  STOP_LOSS vs TAKE_PROFIT, servirà un ulteriore ADR.

## Test gating

`tests/unit/orchestration/test_decision_loop.py::test_check_pending_closures_detects_closure_by_symbol`
— posizione aperta per `symbol="BTC"` (con `hl_position_id=None`), `MockHyperliquidClient`
con `closed_positions={"BTC": PositionClosureInfo(...)}`: verifica che
`_check_pending_closures` **rilevi** la chiusura e chiami `close_position`. Il test usa il
Mock keyed-by-argument, quindi **fallisce se si reintroduce il bug** (lookup su `None` /
guardia `hl_position_id`) e passa con il fix. (Verificato fail-con-bug / pass-con-fix.)

## Propagazione

- [x] `src/aiat/orchestration/decision_loop.py::_check_pending_closures` usa `position.symbol`
- [x] Test con denti aggiunto (`test_check_pending_closures_detects_closure_by_symbol`)
- [x] Indicizzato in `docs/decisions/README.md`
- [x] Identità=symbol validata su testnet reale (M4-T08, 2026-06-28)
- [x] Colonna `positions.hl_position_id` rimossa (migrazione `003_drop_hl_position_id`)
- [ ] (PRD non modificato: frozen; §4.1 step 9 resta valido, qui solo l'identità è ratificata)
