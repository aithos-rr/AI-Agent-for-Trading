# ADR-0014: HOLD/FLAT outcome labeling rule — fee-hurdle counterfactual

**Data**: 2026-06-14
**Status**: accepted
**Milestone**: M4 (ExecutionLayer + OutcomeResolver)
**PRD reference**: §4.2, §15.4
**Closes deferral**: D2

## Contesto

Il PRD V2 §15.4 ha esplicitamente deferito la definizione operativa della regola
di labeling per le decisioni HOLD/FLAT all'implementazione dell'OutcomeResolver
(M4). Il deferral D2 è vincolante: M4 non può chiudersi senza questa regola
fissata, documentata e testata.

Il problema: il Brier score usato in RESEARCH §4.2 per la calibrazione della
confidence richiede un outcome binario `outcome_i ∈ {0, 1}` per ogni decisione.
Per LONG/SHORT, `outcome_i = 1` se `pnl_net_fee_funding_usd > 0`. Per HOLD/FLAT,
non esiste una posizione chiusa, quindi non esiste PnL reale: serve una regola
controfattuale.

La RESEARCH_DESIGN.md §2.1 definisce confidence per HOLD/FLAT come:
> "estimated probability that this passive choice is preferable to the active
> alternatives at this moment."

Tre classi di alternative controfattuali erano candidate:

1. **Best ex-post**: HOLD/FLAT corretto se `max(LONG_pnl, SHORT_pnl) ≤ 0`
2. **Fee-hurdle**: HOLD/FLAT corretto se `|Δprice%| ≤ fee_roundtrip%`
3. **Esclusione**: non creare outcome rows per HOLD/FLAT; escluderle dal Brier score

La scelta richiede di bilanciare completezza del dataset e riproducibilità
del calcolo al momento dell'analisi.

## Decisione

**Regola adottata: fee-hurdle counterfactual** (alternativa 2).

Una decisione HOLD/FLAT riceve `was_profitable_net = True` se e solo se la
variazione assoluta del prezzo di riferimento sull'orizzonte temporale
`time_horizon_min` non supera il tasso di costo round-trip (`fee_roundtrip_pct`):

```
abs((price_at_horizon - price_at_decision) / price_at_decision) ≤ fee_roundtrip_pct
```

In altri termini: nessuna posizione direzionale avrebbe superato il drag di
commissioni. Il valore di default per `fee_roundtrip_pct` è `Decimal("0.002")`
(0.1% taker × 2 lati = 0.2%) — allineato al modello di fee di Hyperliquid.

Per le outcome rows HOLD/FLAT:
- Tutti i campi PnL = `Decimal("0")` (nessuna posizione aperta)
- `holding_duration_min = decision_action_time_horizon_min`
- `horizon_met = True` (la scelta passiva è stata mantenuta per l'intero orizzonte)
- `pnl_net_fee_funding_tax_sim_usd = Decimal("0")` (popolato da tax sim, mai dal resolver)

Implementato in `OutcomeResolver.resolve_hold_flat()` in
`src/aiat/execution/outcome_resolver.py`.

## Conseguenze

### Positive
- Tutti i decision_actions (LONG/SHORT/HOLD/FLAT) hanno un outcome row: dataset
  completo per Brier score senza esclusioni arbitrarie.
- La regola è semplice, deterministica, e riproducibile da soli prezzi in
  `context_snapshots` (già persistiti).
- Simmetria: HOLD/FLAT è etichettato "corretto" quando la volatilità è bassa —
  semanticamente coerente con confidence come "probabilità che la scelta passiva
  sia preferibile alle attive".

### Negative
- La fee-hurdle rule non distingue tra "HOLD era giusto perché ho previsto la
  direzione sbagliata" e "HOLD era giusto perché il mercato era laterale". Questo
  introduce imprecisione nella calibrazione.
- Richiede `price_at_horizon` dal context_snapshot corrispondente; se il
  snapshot non è disponibile, l'outcome HOLD/FLAT non può essere risolto. In
  questo caso si lascia l'outcome row non creata e si logga un warning strutturato.

### Neutre (trade-off accettati)
- `horizon_met = True` per HOLD/FLAT è una convenzione, non un fatto osservato.
  È documentata qui e nel docstring del metodo.
- `holding_duration_min = time_horizon_min` è convenzione analoga. Il significato
  reale è "la scelta passiva è durata tutta la finestra di osservazione".

## Alternative considerate

### Alternativa A: best ex-post comparison
- `was_profitable_net = True` iff `max(LONG_pnl_net, SHORT_pnl_net) ≤ 0`
- Pro: risponde esattamente a "era meglio delle alternative?"
- Contro: richiede simulare due posizioni ipotetiche con size/leverage arbitrari;
  la scelta dei parametri è un'assunzione non ovvia e introduce un grado di
  libertà nell'analisi.
- Scartata perché introduce un'ipotesi non necessaria su size/leverage.

### Alternativa C: esclusione HOLD/FLAT dal Brier score
- Non creare outcome rows per HOLD/FLAT; Brier score calcolato solo su LONG/SHORT.
- Pro: nessun dato sintetico nel database.
- Contro: con il 30-50% di HOLD stimato dal RESEARCH_DESIGN §5, si perde la
  maggioranza delle osservazioni per la calibrazione; bias sistematico verso
  modelli che tradano più spesso.
- Scartata perché incompatibile con la definizione di confidence per HOLD/FLAT
  data dal RESEARCH_DESIGN §2.1 (confidence ≠ NULL anche per HOLD/FLAT — invariante #7).

## Test gating

`tests/unit/execution/test_outcome_resolver.py` — 24 test coprendo:
- LONG/SHORT: PnL computation, funding sign, zero PnL boundary, horizon_met bounds
- HOLD/FLAT: fee threshold, simmetria su/giù, boundary inclusivo, campi PnL a zero,
  holding_duration = time_horizon, horizon_met = True

## Propagazione

- [x] Implementato in `src/aiat/execution/outcome_resolver.py`
- [x] Testato in `tests/unit/execution/test_outcome_resolver.py`
- [x] Indicizzato in `docs/decisions/README.md`
- [ ] OutcomesRepository (M5-T02b) deve passare `fee_roundtrip_pct` a `resolve_hold_flat`
      leggendolo dalla config o da una costante in `execution/outcome_resolver.py`
- [ ] `tests/e2e/test_isolation.py` e `test_guardrail_e2e.py` (M5) devono verificare
      che le outcome rows HOLD/FLAT abbiano `was_profitable_net` calcolato dalla regola
