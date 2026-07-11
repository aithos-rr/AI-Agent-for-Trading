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
| 0022 | [m5t14-real-llm-smoke](0022-m5t14-real-llm-smoke.md) — M5-T14 con LLM reali + HL testnet (non mock): il mock nasconde il confound formato structured-output provider-specifico; Opzione 2 (un agent/volta, 1 wallet reale, swap address DB); concorrenza 4-agent coperta da e2e | accepted | 2026-06-29 | M5-T14 | none |
| 0023 | [provider-aware-sampling](0023-provider-aware-sampling.md) — client provider-aware sui sampling param: Anthropic Opus 4.8 (thinking-only) rifiuta `temperature` (HTTP 400, M5-T14) → omessa; asimmetria di determinismo cross-model dichiarata (limite tesi); corregge ADR-0020 | accepted | 2026-06-29 | M5-T14/M7 | none |
| 0024 | [per-action-execution-isolation](0024-per-action-execution-isolation.md) — isolamento errori esecuzione per-azione (un ordine rifiutato non aborta il tick → run PARTIAL) + tassonomia `execution_status` (HOLD/no-op→not_applicable, filled→FILLED+executed, rejected→FAILED+error) via `mark_action_execution`; bug bookkeeping stanato da M5-T14; nessuna migrazione | accepted | 2026-06-29 | M5-T14/M7 | none |
| 0025 | [flip-atomicity-and-reconciliation](0025-flip-atomicity-and-reconciliation.md) — flip (close→open) = due market order sequenziali **non atomici** chain↔DB; **detection + alert** riconciliazione DB↔`clearinghouseState` implementata in M6.2 (`chain_reconciliation.py` + `_reconcile_chain_state` → riga `errors` `ChainDivergence` a inizio tick, il tick prosegue); **auto-repair** ancora deferito (criteri nell'ADR); chiude P3 di ADR-0030 | accepted (detection M6.2; auto-repair deferito) | 2026-07-07 | M5-T14/M6.2/M7 | none |
| 0026 | [a7-lightweight-ping](0026-a7-lightweight-ping.md) — probe credenziali A7 lightweight `ping()` (raw `_llm.ainvoke`, NO structured output) su `BaseLLMClient`; riallinea A7 a PRD §10.1 (l'`invoke`→`invoke_structured`→`TradeDecision` era la deviazione); chiude il FOLLOW-UP di `lifecycle.py`; sblocca per costruzione OpenAI/DeepSeek/Qwen; `invoke_structured`/`json_schema` intatti (scope M6, ADR-0008) | accepted | 2026-07-01 | M5-T14/M6/M7 | none |
| 0027 | [flat-close-bookkeeping-gap](0027-flat-close-bookkeeping-gap.md) — path chiusura FLAT persiste parzialmente: manca riga `orders` `order_kind='close'`, `positions.closing_action_id` resta NULL, `chk_position_closed_consistency` non richiede `closing_action_id` sul ramo chiuso; scoperto tick-2 Agent OpenAI (SOL chiusa on-chain, PnL +0.201); stessa famiglia di ADR-0024; fix (order close + closing_action_id + migration CHECK) in sessione dedicata; NON blocca M5-T14, **BLOCCA M6** (dataset) | accepted | 2026-07-01 | M5-T14/M6 | none |
| 0028 | [openai-json-schema-fallback-variance](0028-openai-json-schema-fallback-variance.md) — verifica anticipata direct-provider (ADR-0008): `json_schema` su OpenAI `gpt-4.1-mini` diretto funziona ma con varianza residua sotto temp=0+seed (~2/8 via fallback, 0 fallimenti irrecuperabili); `fallback_used` = metrica sperimentale per-modello (non allarme); limite determinismo da dichiarare in RESEARCH §7; Qwen/DeepSeek da osservare; `structured.py` intatto | accepted | 2026-07-01 | M5-T14 | none |
| 0029 | [structured-output-provider-aware](0029-structured-output-provider-aware.md) — structured output **provider-aware**: il confine dev/direct di ADR-0008 (`invoke_structured`) si materializza sull'accesso diretto; gemello di ADR-0023 (sampling) e ADR-0028 (fallback variance); vincolante M6/M7 | accepted | 2026-07-06 | M5-T14/M6/M7 | none |
| 0030 | [close-path-followups-check-and-sltp-callsite](0030-close-path-followups-check-and-sltp-callsite.md) — follow-up di ADR-0027: (P1) `chk_position_closed_consistency` reso **condizionale** su `close_reason` (SL/TP/liquidated ammettono `closing_action_id` NULL), (P2) call-site `_check_pending_closures` allineato alla firma a 5 arg + attribuzione SL/TP per-lato; P1+P2 fixati `b65e833` (2026-07-07); P3 (flip) → ADR-0025 | accepted | 2026-07-06 | M5-T14/M6 | none |
| 0031 | [funding-ledger](0031-funding-ledger.md) — funding ledger (finding B): job orchestrator 8h legge `userFunding` HL per wallet → riga `FundingEvent` per pagamento orario contro la posizione aperta; idempotente `(position_id, period_end)` senza migration; `HLPublicInfoClient.user_funding_history`; `outcomes.sum_funding_usd` già lo somma | accepted | 2026-07-11 | M6.2 | none |
| 0032 | [autonomous-close-fee](0032-autonomous-close-fee.md) — chiude ADR-0030 (iv) per SL/TP: la chiusura autonoma persiste il `FeeEvent` (`taker_close`) da `PositionClosureInfo.fee_usd` (valorizzata in 51a8e45) linkato all'ordine trigger scattato → entra in `sum_fees_usd`; liquidazione resta deferita (nessun ordine da linkare, ADR-0025); nessuna migration | accepted | 2026-07-11 | M6.2 | none |
| 0033 | [tax-sim-writer](0033-tax-sim-writer.md) — writer/job tax-sim: job orchestrator giornaliero (`TaxSimRunner`) aggrega gli `outcomes` del periodo chiuso per modello via `TaxSimulationRepository` esistente; periodo `daily`/`quarter` via env; rate 0.33 (regime IT leva) come override di config esplicito (server_default schema 0.26 invariato, nessuna migration); idempotente su UNIQUE `(exp,model,quarter_label)` | accepted | 2026-07-11 | M6.2 | none |

## Template

Vedi `0000-template.md` per il template ADR standard.
