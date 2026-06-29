# Architecture Decision Records (ADR)

Questa directory contiene gli ADR del progetto AI Trading Agent V2.

## Cosa è un ADR

Un Architecture Decision Record documenta una decisione architetturale o
implementativa che si discosta dal `PRD_V2.md` (frozen) o lo estende/raffina
sulla base di evidenze emerse durante l'implementazione.

Gli ADR sono **immutabili** una volta in stato `accepted`. Se una decisione
viene sostituita, si crea un nuovo ADR con `Status: supersedes ADR-XXXX`,
e l'originale viene marcato `superseded by ADR-YYYY`.

## Quando creare un ADR

- Deviazione dal PRD V2 (cambia comportamento o struttura rispetto a quanto
  documentato)
- Chiusura di una *bounded deferral* di PRD §15.4 (D1-D5)
- Decisione implementativa non coperta dal PRD che ha implicazioni durature
  (non per micro-scelte locali di una funzione)

## Quando NON creare un ADR

- Implementazione che segue fedelmente il PRD V2 (è già documentato lì)
- Refactoring interno senza cambio di API esposta
- Bug fix puntuali con test che riproduce il bug

## Convenzione naming

`NNNN-titolo-breve-kebab-case.md` dove NNNN è il numero progressivo a 4 cifre.

## Indice degli ADR accettati

| ID | Titolo | Status | Data | Milestone | Closes deferral |
|----|--------|--------|------|-----------|-----------------|
| 0001 | Adozione del pattern ADR per Phase 5 | accepted | 2026-05-14 | M0 | none |
| 0006 | Conteggio tabelle DB — 20, non 17 | accepted | 2026-06-13 | M1 | none |
| 0007 | Set repository — §7.6 autoritativo, niente `ledger.py` | accepted | 2026-06-13 | M1, M3, M4, M5 | none |
| 0008 | Routing LLM dual-mode — OpenRouter (sviluppo) / provider diretti (esperimento) | accepted | 2026-06-13 | M2 | none |
| 0009 | [exception-classification](0009-exception-classification.md) — isinstance() primario + string-match fallback | accepted | 2026-06-13 | M2 | D3 |
| 0010 | [vcr-cassette-recording](0010-vcr-cassette-recording.md) — meccanismo cassette, slug OpenRouter, limiti cost ledger | accepted | 2026-06-14 | M2 | none |
| 0011 | [rss-sources](0011-rss-sources.md) — 10 items/tick, 2 fonti RSS pubbliche (CoinDesk + Cointelegraph; CryptoPanic dismesso, sostituito 2026-06-29), fallimento parziale tollerato | accepted | 2026-06-14 | M3 | D5 |
| 0012 | [controlled-signals](0012-controlled-signals.md) — 18 segnali §6.2 adottati come vocabolario finale | accepted | 2026-06-14 | M3 | D4 |
| 0013 | [onchain-semantics-and-news-robustness](0013-onchain-semantics-and-news-robustness.md) — funding ×8, premium al posto di long_short_ratio, news follow_redirects + parser tollerante | accepted | 2026-06-14 | M3 | none |
| 0014 | [holdflat-outcome](0014-holdflat-outcome.md) — HOLD/FLAT outcome labeling: fee-hurdle counterfactual, was_profitable_net=True iff \|Δprice%\| ≤ fee_roundtrip% | accepted | 2026-06-14 | M4 | D2 |
| 0015 | [size-units-convention](0015-size-units-convention.md) — `size_units` = quantità leveraged eseguita (devia da §9.2 r.2346; reconcilia sizing.py con positions.py) | accepted | 2026-06-14 | M4 | none |
| 0016 | [position-identity-symbol](0016-position-identity-symbol.md) — identità posizione = coin symbol; fix bug rilevazione chiusure SL/TP; `hl_position_id` **rimossa** (migr. 003) dopo conferma M4-T08 | accepted | 2026-06-28 | M4/M5 | none |
| 0017 | [size-quantization](0017-size-quantization.md) — size ordine quantizzata a `szDecimals` (ROUND_DOWN) al confine SDK; bug `float_to_wire` stanato da M4-T08; guard size-zero; notional eseguito ≤ richiesto | accepted | 2026-06-28 | M4 | none |
| 0018 | [price-quantization](0018-price-quantization.md) — prezzo trigger SL/TP quantizzato alla regola nativa HL perp (`round(f"{px:.5g}", 6−szDecimals)`, al più vicino); bug `Invalid TP/SL price` stanato da M4-T08 round 2; distinta da ADR-0017 (size, ROUND_DOWN) | accepted | 2026-06-28 | M4 | none |
| 0019 | [context-network](0019-context-network.md) — context su `settings.network` (testnet, fix confound technical-mainnet vs onchain-testnet); + fix bug tick job zero-arg su orchestrator E agent, `current_tick()` allinea `tick_id` al boundary 15m (inv #13); sblocca M3-T11/M5-T14 | accepted | 2026-06-29 | M3/M4 | none |
| 0020 | [model-structure](0020-model-structure.md) — struttura 4 modelli LLM (D1-struttura): matrice provider×geography×tier, tier=costo assoluto di mercato, id stabili (usa/cn-premium/cheap), temp=0/seed=42; nomi commerciali al seed M7; 3 limiti dichiarati | accepted | 2026-06-29 | M5/M6 | D1 (parziale) |
| 0021 | [single-seed-script](0021-single-seed-script.md) — `seed_experiment.py` unico idempotente (experiment+4 models+prompt_template+3 baselines) invece di due script separati; hash del template calcolato una volta sola → niente `prompt_template_hash` divergente (A5) | accepted | 2026-06-29 | M5/M6 | none |

## Template

Vedi `0000-template.md` per il template ADR standard.
