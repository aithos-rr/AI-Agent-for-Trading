# ADR-0012: Vocabolario finale `controlled_signals` — 18 valori §6.2

**Data**: 2026-06-14
**Status**: accepted
**Milestone**: M3 (ContextOrchestrator + collectors)
**PRD reference**: §6.2, §3.2.1, §15.4
**Closes deferral**: D4

## Contesto

PRD §15.4 defer D4 afferma: "La lista preliminare di 18 valori in §6.2 può
richiedere raffinamento dopo aver osservato cosa generano i 4 LLM in smoke
test".  Il deferral deve essere chiuso in M3 (ContextOrchestrator), perché
`prompt_template_hash` (§3.2.1) dipende dal vocabolario —  se il vocabolario
cambiasse dopo il seed (M7 step 4) l'hash divergerebbe e l'invariante di
comparabilità cross-modello (inv #6) sarebbe violato.

I 4 collector implementati (M3-T02…T05) producono dati allineati ai 18
segnali:
- `TechnicalIndicators` → `technical.*` (RSI, MACD, EMA, Bollinger, ATR,
  support/resistance)
- `SentimentSnapshot` → `sentiment.*` (news polarity, fear&greed, market panic)
- `OnChainSnapshot` → `onchain.*` (funding, OI, liquidation cascade)
- `PortfolioState` / market data → `market.*` + `portfolio.*`

Nessuna evidenza da smoke test è ancora disponibile, ma il vocabolario §6.2 è
stato già progettato in accordo con le sorgenti dati e con la struttura del
prompt (§3.2.1 `controlled_signals` JSONB).

## Decisione

Adottiamo il vocabolario §6.2 **esatto, senza modifiche**, come lista finale.
18 segnali, 5 categorie:

| Categoria     | Segnali |
|---------------|---------|
| `technical`   | rsi_extreme, macd_cross, ema_alignment, bollinger_squeeze, atr_spike, support_resistance |
| `sentiment`   | news_polarity, fear_greed, market_panic |
| `onchain`     | funding_rate_extreme, open_interest_shift, liquidation_cascade |
| `market`      | volatility_regime, volume_anomaly, basis_perp_spot |
| `portfolio`   | exposure_high, unrealized_pnl, position_aging |

Il vocabolario vive in `src/aiat/context/controlled_signals.py` come
`CONTROLLED_SIGNALS: frozenset[str]`.  Il `ControlledSignal = Literal[...]`
in `domain/schemas.py` rimane la source-of-truth per type-checking; il test
`test_controlled_signals.py` garantisce la parità a CI.

## Conseguenze

### Positive
- `prompt_template_hash` può essere calcolato e committato nel seed M7.
- Il vocabolario è documentato in un posto solo (`controlled_signals.py`) e
  verificato automaticamente in CI.
- Allineamento immediato con i 4 collector già implementati — nessuna
  riscrittura richiesta.

### Negative
- Se smoke test in M3-T11 rivelasse che i modelli usano segnali non coperti,
  occorrerebbe un ADR sostitutivo + aggiornamento dell'hash e del seed, con
  ripercussioni sul calendario M6/M7.

### Neutre (trade-off accettati)
- 18 segnali è un compromesso tra espressività e dimensione del prompt.
  Modelli con context window ridotto (Qwen 7B) non sono penalizzati: la lista
  è ~250 token a testo libero, trascurabile rispetto al bundle completo.

## Alternative considerate

### Alternativa A: Aspettare smoke test M3-T11 prima di chiudere D4
- Pro: vocabolario basato su evidenza empirica.
- Contro: `prompt_template_hash` non può essere calcolato prima del seed; il
  loop non può progredire verso M4 senza D4 chiusa.
- Scartata perché il timing del deferral (M3) era già stato scelto dal PRD
  consapevolmente.

### Alternativa B: Ridurre a 12 segnali (eliminare `portfolio.*`)
- Pro: prompt più corto.
- Contro: i segnali `portfolio.exposure_high`/`unrealized_pnl`/`position_aging`
  sono necessari per la coerenza con `PortfolioState` (§6.3) e per il
  guardrail #3 (leverage × confidenza) in M4.
- Scartata.

## Test gating

`tests/unit/context/test_controlled_signals.py::test_controlled_signals_matches_literal`
asserta `set(get_args(ControlledSignal)) == CONTROLLED_SIGNALS` — fallisce in
CI se le due definizioni divergono.

## Propagazione

- [x] Implementato in `src/aiat/context/controlled_signals.py`
- [x] Test in `tests/unit/context/test_controlled_signals.py`
- [ ] Seed M7: inserire `CONTROLLED_SIGNALS` nel calcolo di `prompt_template_hash`
      quando si esegue `python -m aiat seed_experiment`
