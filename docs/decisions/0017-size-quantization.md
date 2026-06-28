# ADR-0017: Quantizzazione size a `szDecimals` (ROUND_DOWN) al confine SDK

**Data**: 2026-06-28
**Status**: accepted
**Milestone**: M4 (ExecutionLayer), emersa da M4-T08 (primo contatto testnet reale)
**PRD reference**: §7.5, §9.2, §3.2.4; ADR-0015 (size_units leveraged)
**Closes deferral**: none (chiude un'assunzione SDK implicita scoperta sul campo)

## Contesto

Il **primo run reale** di `tests/e2e/test_testnet_smoke.py` contro la Hyperliquid testnet
(M4-T08, in WSL) è fallito in fase di firma ordine:

```
ValueError("float_to_wire causes rounding", 0.0012814444551819603)
  at hyperliquid/utils/signing.py:478
```

**Causa**: `compute_position_sizing` (`sizing.py`) calcola
`size_units = notional / entry_price` a **precisione Decimal piena** (fino a 19 cifre
decimali). Hyperliquid rifiuta qualunque size con **più cifre decimali di quelle ammesse
dall'asset** (`szDecimals`, definito dal venue per ogni perp). Per BTC perp `szDecimals=5`,
quindi `0.0012814444551819603` è invalido: deve essere `0.00128`.

Questa è la **6ª assunzione SDK**, finora **implicita e non documentata**: il
`MockHyperliquidClient` non emula la quantizzazione `szDecimals`, perciò il client reale non
l'aveva mai implementata e i test (mock) non l'avevano mai esercitata. È esattamente il tipo
di gap che solo il contatto con il venue reale (M4-T08) poteva stanare.

## Decisione

**La size di un ordine viene quantizzata a `szDecimals` cifre decimali con `ROUND_DOWN`, al
confine SDK (dentro il client `RealHyperliquidClient`), subito prima dell'invio.**

- `szDecimals` è letto **live dal venue** via
  `self._info.asset_to_sz_decimals[self._info.name_to_asset(symbol)]` (popolato all'init di
  `Info` dai metadati del perp) — **mai hardcodato**.
- La quantizzazione resta in **`Decimal`** (inv #12): `size.quantize(Decimal(1).scaleb(
  -szDecimals), rounding=ROUND_DOWN)`. Il `float()` avviene **solo** nell'ultimo passaggio a
  `market_open` / `order`, com'era già.
- **`ROUND_DOWN` (non al più vicino)**: garantisce che il notional eseguito non **superi**
  mai quello richiesto da `size_pct·equity·leverage`, preservando il guardrail `max_size_pct`
  (inv #8). Lo scarto è ≤ il valore di **un passo di size** dell'asset (`10^-szDecimals`
  unità · prezzo).
- **Guard size-zero**: se la size quantizzata è `0` (notional troppo piccolo rispetto al
  passo dell'asset), il client solleva `ExecutionRejectedError` (con symbol, size teorica,
  `szDecimals`) invece di inviare un ordine da 0 unità.
- Applicata a **ogni confine size→SDK**: l'entry (`market_open`) e gli ordini trigger SL/TP
  (`order`). `market_close` non passa una size (chiude l'intera posizione).

`sizing.py` resta **logica pura, venue-agnostica**: la quantizzazione **NON** va lì (la
precisione del venue è un dettaglio di esecuzione, non di dominio).

### Fonte di verità per la size persistita

La `Position` persiste `filled_size_units` = la quantità **realmente riempita on-chain**
(`totalSz` dalla risposta HL), non quella teorica — così `entry_price · size_units`
riconcilia con il notional reale e l'identità PnL dell'outcome regge. L'entry `OrderResult`
porta `requested_size_units = size quantizzata inviata` e `filled_size_units = totalSz`
(fill). `PositionsRepository.open_position` usa `filled_size_units` come fonte di verità.

## Conseguenze

### Positive
- Gli ordini sono accettati dal venue (bug `float_to_wire` risolto alla radice).
- Notional eseguito ≤ richiesto (guardrail mai violato per arrotondamento).
- `szDecimals` dal venue → robusto a nuovi asset / cambi di tick size.

### Negative / Note (validità scientifica)
- Il **notional eseguito può essere leggermente inferiore** a quello richiesto dal modello
  (`size_pct·equity·leverage`); lo scarto è **noto e limitato** (≤ un passo di size). Va
  **menzionato nel capitolo metodologico** della tesi come scostamento di esecuzione
  controllato (non un bias sistematico sul segno; riduce marginalmente l'esposizione).
- Per notional molto piccoli rispetto al passo dell'asset, l'ordine è rifiutato
  (`ExecutionRejectedError`) anziché eseguito a size 0 — comportamento corretto, ma da tenere
  presente per asset con `szDecimals` basso.

## Test gating

`tests/unit/execution/test_real_hyperliquid_client.py` — unit del nuovo helper di
quantizzazione (SDK mockato, no rete): `szDecimals=5` → `0.0012814444551819603` → `0.00128`;
troncamento ROUND_DOWN; size che si azzera → `ExecutionRejectedError`; symbol sconosciuto →
`ExecutionRejectedError`. La validazione reale del fix gira in WSL (M4-T08), non nel container.

## Propagazione

- [x] `src/aiat/execution/hyperliquid_client.py`: helper `_quantize_size`/`_sz_decimals` +
      applicazione a entry e trigger SL/TP + guard size-zero
- [x] Unit test del helper
- [x] Indicizzato in `docs/decisions/README.md`
- [ ] `sizing.py` invariato (resta puro, venue-agnostico — by design)
- [ ] Validazione testnet reale del fix → M4-T08 (gira in WSL; M4-T08 resta aperto)
