# ADR-0015: `size_units` = quantità eseguita leveraged (deviazione da §9.2 r.2346)

**Data**: 2026-06-14
**Status**: accepted
**Milestone**: M4 (ExecutionLayer), vincolante per M5 (decision_loop wiring)
**PRD reference**: §9.2 (riga 2346), §3.2.4 (DDL `positions`), §7.5/§7.6
**Closes deferral**: none (correzione di auto-contraddizione interna al PRD)

## Contesto

La review adversariale di M4 (gate M4) ha rilevato che il campo `size_units` ha **due
semantiche divergenti** nel codice, ciascuna avallata da una parte diversa del PRD V2
(che è quindi internamente contraddittorio):

- **`execution/sizing.py`** (calcolatore pre-trade) usava `size_units = margin / price`
  (quantità **unleveraged**) e `notional = price · size_units · leverage`. Questo segue
  **alla lettera la formula §9.2 riga 2346** (`notional_value_usd = price * size_units * leverage`).
- **`db/repositories/positions.py`** (unico writer reale delle colonne) usa
  `size_units = entry_order.filled_size_units` (la quantità **leveraged** effettivamente
  riempita on-chain da Hyperliquid), `notional = size_units · price`,
  `margin = notional / leverage`. Questo segue il DDL §3.2.4 e la realtà di esecuzione.

Le due convenzioni usano lo **stesso nome di colonna** con significati diversi: se
`sizing.py` fosse mai stato usato per submittare un ordine, avrebbe aperto una posizione
**leverage× troppo piccola**, e i record `notional_value_usd` / `initial_margin_usd`
sarebbero stati incoerenti tra i due moduli. Tocca metriche centrali per la tesi
(notional, exposure, PnL) → **validità scientifica**.

Stato al momento della decisione: `compute_position_sizing` è **dead code** (chiamato solo
dai suoi unit test, mai da `positions.py` né da alcun servizio; `__main__.py` è stub M0).
Quindi nessun dato è stato corrotto; il difetto era latente.

Poiché il PRD si auto-contraddice (§9.2 r.2346 vs §3.2.4 + §7.5), la scelta è stata
**sottoposta all'utente** (decisione di design che tocca la validità scientifica, regola
CLAUDE.md "non inventare; se ambiguo, chiedi"). L'utente ha scelto la convenzione leveraged.

## Decisione

**`size_units` denota la quantità di asset LEVERAGED effettivamente detenuta sull'exchange**
(ciò che Hyperliquid riempie). Convenzione canonica = quella di `PositionsRepository`:

```
initial_margin_usd  = equity_usd · size_pct
notional_value_usd  = initial_margin_usd · leverage
size_units          = notional_value_usd / entry_price
                    = (equity_usd · size_pct · leverage) / entry_price
```

Da cui valgono (per costruzione, lato `positions.py` sul fill reale):
`notional_value_usd = size_units · entry_price` e `initial_margin_usd = notional_value_usd / leverage`.

`execution/sizing.py` è stato corretto per emettere questa convenzione. I suoi unit test
sono stati aggiornati di conseguenza (es. equity 1000 / size_pct 0.10 / price 100 /
leverage 2 → margin 100, notional 200, **size_units 2** — non più 1).

**Questo devia dalla formula letterale di §9.2 riga 2346** (`notional = price · size_units ·
leverage`), che double-counta la leva quando `size_units` è la quantità leveraged. §9.2
riga 2346 è da considerarsi superata da questo ADR; prevalgono §3.2.4 (DDL) e §7.5.

## Conseguenze

### Positive
- Coerenza end-to-end: `sizing.py` (pre-trade) e `positions.py` (persistenza dal fill)
  usano la stessa semantica di `size_units`; `notional`/`margin` riconciliano.
- `size_units` rappresenta l'esposizione reale: un movimento di prezzo di $1 su una
  posizione di N unità leveraged produce $N di PnL (corretto per l'analisi).

### Negative / Note
- Deviazione testuale da §9.2 r.2346 (PRD frozen) — tracciata qui.
- Precisione Decimal: `size_units = notional / price` può non terminare; vale esattamente
  `notional = margin · leverage` e `size_units = notional / price` (definizione), mentre
  `notional == size_units · price` è esatto solo quando la divisione termina. I test
  asseriscono le relazioni esatte (no round-trip lossy).

## Lavoro rinviato a M5 (vincolante prima del wiring di esecuzione)

- **`MockHyperliquidClient._open_orders`** mette ancora `action.size_pct` in
  `requested_size_units` / `filled_size_units` (placeholder, come `filled_price=100`).
  **NON** è un percorso dati reale (il `decision_loop` M5 che collega
  `execute_action → open_position` non esiste ancora). Quando M5 cabla il loop, la
  conversione `size_pct → size_units` deve avvenire **via `compute_position_sizing`**
  (usando equity dal `portfolio_state` + mid price + leverage), e il client HL reale +
  il relativo test vanno aggiornati a questa convenzione. Finding DEFERRED della review M4.

## Test gating

`tests/unit/execution/test_sizing.py` — verifica la convenzione leveraged:
`notional = size_units · price`, `size_units = (equity · size_pct · leverage) / price`,
`margin = notional / leverage`, più i tipi Decimal (inv #12) e SL/TP LONG/SHORT.
`tests/integration/test_db_repositories_positions.py:270` già asserisce
`notional = size_units · price` (no leva) lato repository.

## Propagazione

- [x] `src/aiat/execution/sizing.py` corretto + docstring
- [x] `tests/unit/execution/test_sizing.py` aggiornato
- [x] Indicizzato in `docs/decisions/README.md`
- [x] M5: `MockHyperliquidClient._open_orders` usa `compute_position_sizing` per la
      conversione `size_pct → size_units` (equity da `portfolio_state`, mock entry
      price `100.00`, leverage da action). Il client HL reale resta lavoro futuro a
      M4-T08; il `decision_loop` non chiama direttamente `compute_position_sizing` (la
      conversione avviene nel client).
- [ ] (PRD non modificato: frozen; §9.2 r.2346 superata da questo ADR)
