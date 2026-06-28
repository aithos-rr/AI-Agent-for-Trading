# ADR-0018: Quantizzazione prezzo trigger SL/TP alla regola nativa HL (perp)

**Data**: 2026-06-28
**Status**: accepted
**Milestone**: M4 (ExecutionLayer), emersa da M4-T08 round 2 (testnet reale)
**PRD reference**: §7.5; ADR-0015 (size leveraged), ADR-0017 (quantizzazione size)
**Closes deferral**: none (chiude una 7ª assunzione SDK implicita scoperta sul campo)

## Contesto

Dopo ADR-0017 (size quantizzata a szDecimals), il run testnet di
`tests/e2e/test_testnet_smoke.py` (M4-T08, WSL) ha **superato l'ENTRY** — l'ordine LONG BTC
ha firmato e riempito — ma è fallito sui **trigger SL/TP**:

```
ExecutionRejectedError: HL order error: 'Invalid TP/SL price. asset=3'
```

**Causa**: `_trigger_order` quantizzava la **size** (ADR-0017) ma passava il **prezzo** grezzo.
`sizing.stop_loss_price` / `take_profit_price` sono `entry_price·(1±pct)` a precisione Decimal
piena; Hyperliquid rifiuta i prezzi che violano la sua regola di formato. È la **7ª assunzione
SDK**: come la size, anche i **prezzi** vanno quantizzati al confine SDK — ma con una regola
**diversa** dalla size.

## Regola HL per i prezzi (replicata, non è API pubblica)

Da `hyperliquid/exchange.py::_slippage_price` (riga ~132), per i perp:

```python
round(float(f"{px:.5g}"), 6 - szDecimals)
```

Un prezzo perp valido ha **max 5 cifre significative** E **max `(6 - szDecimals)` decimali**
(8 per lo spot, ma trattiamo solo perp). Per BTC (`szDecimals=5`): 5 sig-fig e 1 decimale →
a ~$73k il prezzo è di fatto intero (es. `73118.456789 → 73118.0`). I prezzi interi sono
sempre validi.

## Decisione

**I prezzi inviati a HL (trigger SL/TP) sono quantizzati al confine SDK con la regola nativa
HL per i perp: `round(float(f"{px:.5g}"), 6 - szDecimals)`, arrotondamento al più vicino.**
`szDecimals` è letto **live dal venue** (`self._info.asset_to_sz_decimals[name_to_asset(symbol)]`),
mai hardcodato. L'helper replica la formula nativa dell'SDK, garantendo che l'output superi il
check `float_to_wire` dell'SDK stesso.

**Distinta da ADR-0017 (size, ROUND_DOWN)**: qui l'arrotondamento è **al più vicino** (non una
direzione custom) perché:
- lo scarto è ≤ mezzo tick (~0.001% su BTC) → trascurabile sui livelli di rischio SL/TP;
- la coerenza con la quantizzazione prezzo nativa dell'SDK è preferibile a una direzione
  custom (che potrebbe comunque non passare `float_to_wire`).

`sizing.py` resta **logica pura**: calcola i prezzi teorici esatti (`entry·(1±pct)`); la
quantizzazione venue-specifica vive nel client.

### `requested_price` nell'OrderResult

Coerente con ADR-0017 (dove `requested_size_units` = size quantizzata): l'OrderResult del
trigger registra come `requested_price` il prezzo **quantizzato realmente inviato**, così il
record riflette ciò che è stato davvero trasmesso all'exchange (non il teorico).

## Conseguenze

### Positive
- I trigger SL/TP sono accettati dal venue (bug `Invalid TP/SL price` risolto alla radice).
- Robusto a nuovi asset / tick diversi (szDecimals dal venue).

### Negative / Note (validità scientifica)
- I prezzi SL/TP **effettivi** differiscono di **≤ mezzo tick** da quelli teorici di
  `sizing.py`. Scarto **noto e limitato**, simmetrico (al più vicino), da **citare nel
  capitolo metodologico** della tesi insieme allo scarto di size (ADR-0017). Non introduce
  bias direzionale sistematico.

## Test gating

`tests/unit/execution/test_real_hyperliquid_client.py` — unit di `_quantize_price` (SDK
mockato, no rete): casi a szDecimals diversi (BTC `73118.456789 → 73118.0`; `180.456789` szd2
`→ 180.46`; `1.2814444` szd5 `→ 1.3`), prezzo intero invariato, e asserzione che l'output
**supera `hyperliquid.utils.signing.float_to_wire`** (il check nativo che falliva). La
validazione reale gira in WSL (M4-T08).

## Propagazione

- [x] `src/aiat/execution/hyperliquid_client.py`: helper `_quantize_price` + applicazione in
      `_trigger_order` (triggerPx + limit_px) + `requested_price` quantizzato
- [x] Unit test del helper (+ float_to_wire)
- [x] Indicizzato in `docs/decisions/README.md`
- [ ] `sizing.py` invariato (prezzi teorici esatti; quantizzazione venue-specifica nel client)
- [ ] Validazione testnet reale del fix → M4-T08 (gira in WSL; M4-T08 resta aperto)
