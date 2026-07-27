# Nota metodologica — Dataset M6.1 (esperimento 55555555-5555-5555-5555-555555555555)

**Stato:** bozza in revisione · **Periodo coperto:** 2026-07-07 → 2026-07-27 · **Ruolo del dataset:** smoke test infrastrutturale (M6.1). Questo dataset NON è il dataset di tesi: la raccolta dati per le RQ avverrà su un esperimento nuovo (M7), avviato dopo il gate M6.2 con tutti i fix qui documentati già deployati. Il dataset M6.1 resta archiviato nel DB per trasparenza e riproducibilità della cronaca di sviluppo.

## 1. Scopo e limiti d'uso

Il dataset M6.1 è stato prodotto per validare l'infrastruttura di produzione (Railway, 6 servizi, Hyperliquid testnet) e ha svolto la sua funzione principale: far emergere sotto carico reale i difetti che i test non avevano catturato. Ogni difetto elencato sotto è stato diagnosticato con verifica on-chain, corretto nel codice (commit su `feat/m0-setup`, ADR dedicati) e, dove sensato, riparato nei dati. **Nessuna analisi comparativa tra modelli deve essere condotta su questo dataset**: le anomalie documentate (periodi di inattività asimmetrici, fee gonfie su un sottoinsieme di righe, outcome sintetici corretti ex-post) lo rendono inadatto a qualsiasi inferenza sulle RQ.

## 2. Anomalie note, cause e trattamento

### 2.1 Periodi di inattività di usa-premium (credito API)

usa-premium (Claude Opus 4.8) è rimasto inattivo per esaurimento del credito API Anthropic in due finestre: **19/07 ~01:30 → 24/07 ~14:30 UTC** e **~25/07 sera → fine esperimento** (breve ripresa intermedia il 24–25/07 dopo una ricarica di $5, ~100 run success). Nei periodi di blackout ogni tick produce un run `failed` con `LLMError` (~96/giorno). Conseguenza metodologica: qualsiasi confronto di attività o performance tra modelli su M6.1 è strutturalmente sbilanciato. Per M7: credito verificato su tutti e 4 i provider come precondizione del gate (M6.2-PLAN, P1).

### 2.2 Posizioni zombie (bug T4b) e riparazione

Cinque righe `positions` sono risultate corrotte per il bug T4b (chiusura SL/TP scattata tra i tick non bookkept; doppio meccanismo: closure check eseguito dopo l'esecuzione azioni con short-circuit a livello symbol, e closure check dipendente dal successo del run). Casi, verificati fill-per-fill via `userFills` on-chain:

| Posizione | Modello | Difetto | Correzione applicata |
|---|---|---|---|
| `5b3c555e` | cn-premium BTC | chiusa con outcome sintetico (model_close, exit 63743.3, +2.16) | stop_loss reale: exit 62500, PnL −5.72502, oid 56298713468 |
| `3e6acfe5` | usa-premium BTC | chiusa con exit/fee di un ALTRO ordine (Close Short del 14/07), PnL fabbricato +12.31 | stop_loss reale: exit 62280, PnL −8.784576, oid 56309051125; 6 righe funding post-chiusura riassegnate alla posizione successiva (+0.042771) |
| `da4823d5` | usa-premium BTC | mai chiusa (TP tra i tick + reopen same-symbol) | take_profit: exit VWAP 64056.3, PnL +7.90145, oid 56597441225 |
| `c1624ba0` | usa-premium BTC | mai chiusa (agente inattivo allo scatto) | take_profit: exit VWAP 65567.1, PnL +10.56115, oid 56623016995 |
| `710fe90d` | usa-premium SOL | mai chiusa (agente inattivo allo scatto) | take_profit: exit 75.962, PnL +6.34226, oid 56650748691 |

Riparazione: script one-shot `scripts/repair_zombie_positions.py` (ADR-0035), applicato il 24/07 in transazione unica con pre-state assertion per riga; certificazione: le segnalazioni `ChainDivergence` su usa-premium sono cessate al primo tick post-apply (13:45 UTC). Convenzioni di repair (VWAP multi-fill, closing_run_id, closing_action_id NULL per chiusure autonome, riassegnazione funding) documentate in ADR-0035.

**Due righe NON riparate** (decisione esplicita: dataset archiviato, valore scientifico nullo): `6802457a` (BTC) e `fe09ecde` (SOL), usa-premium, aperte il 24/07 14:46 nella finestra di ripresa e abbandonate al secondo blackout; chiuse on-chain da SL/TP senza bookkeeping. Restano aperte nel DB del vecchio esperimento; le `ChainDivergence` associate (dal ~25/07 in poi) sono rumore di detection atteso. Il fix root-cause (ClosureReconciler a livello orchestrator, ADR-0038) elimina entrambi i meccanismi per M7.

A queste si aggiungono, a fine esperimento, due zombie SOL di usa-cheap con lo stesso pattern T4b: `e06c795c` (long aperta il 26/07 00:15 UTC) e `4feafeae` (long aperta il 27/07 13:45 UTC). Entrambe risultano chiuse sinteticamente dal bookkeeping dell'ultimo tick (27/07 14:30 UTC), che ha attribuito a tutte e due lo stesso evento di chiusura on-chain (fill delle 14:24:10 UTC, exit 75.99): `close_reason` rispettivamente `take_profit` e `stop_loss`, con PnL identico duplicato (−88.73) su entrambe le righe — un "take profit" in perdita è il sintomo evidente del booking sintetico. Anche queste NON sono state corrette: stessa decisione, dataset archiviato.

### 2.3 Convenzione di segno funding (bug corretto il 13/07)

Fino al 13/07 ~14:27 UTC lo storage seguiva la convenzione Hyperliquid (positivo = ricevuto) mentre il resolver degli outcome sottraeva secondo la convenzione PRD §3.2.6 (positivo = pagato): semantica del PnL netto invertita. Fix "negate at ingest" (commit d76a2ff) + riparazione one-shot di 264 righe esistenti (`scripts/flip_funding_signs.py`, eseguito una tantum, non idempotente). Verifica empirica: rate +0.0000125 → amount positivo (pagato) su posizione long. Le righe funding create dopo il fix sono native in convenzione PRD. Diagnostica correlata "riga funding 23:00": falso allarme da offset timezone (CSV export HL in ora locale CEST vs DB UTC), chiusa in ADR-0031.

### 2.4 Fee: copertura parziale e ~10 righe gonfie

(a) **Copertura**: le fee reali da `user_fills` sono persistite solo dal commit 51a8e45 (~11/07): le chiusure precedenti (~190 outcomes) hanno `sum_fees_usd=0`. Backfill eseguito il 2026-07-27 con `scripts/backfill_fees.py` (`--execute`): 396 fee events inseriti da `user_fills` on-chain, meno 2 duplicati rimossi manualmente (ordini SL oid 56309051125 e 56298713468, le cui fee erano già state corrette dal repair ADR-0035) = 394 righe nette.
(b) **Fee gonfie su chiusure SL/TP**: il path di bookkeeping delle chiusure autonome sommava tutti i fill recenti del wallet sul coin invece dei soli fill dell'ordine trigger → ~10 righe `fee_events` con rate 10–50× il tier (e closedPnl potenzialmente contaminato sulle stesse righe). Fix per-oid (commit 8411576); righe storiche lasciate as-is, enumerate via SQL in ADR-0032. Tier fee verificato empiricamente: taker 0.00045 (0.045%) su 518 righe pulite.

### 2.5 Schema-compliance divergente tra modelli (dato, non bug)

GPT 4.1 mini (usa-cheap) produce output non conforme allo schema `TradeDecision` in ~9 tick/giorno (~10%), contro occorrenze sporadiche degli altri modelli — errori Pydantic persistiti come `LLMUnrecoverableError`, run `failed` senza effetti collaterali (fallimento a monte dell'esecuzione, verificato). Correlato: 420 decisioni FLAT emesse senza posizione aperta (`execution_status='not_applicable'`) contro 2–9 degli altri tre modelli. Trattamento per M7 (ADR-0037): nessun retry sui failure di validazione (un retry sarebbe trattamento sperimentale asimmetrico); la variabile misurata è la capacità di produrre output utilizzabile entro il protocollo pre-registrato (structured + un fallback freetext, che resta); soglie gate differenziate (C1 ≥95% escludendo schema-failure; ≥85% inclusiva per usa-cheap). La schema-compliance first-shot resta osservabile via `fallback_used`.

### 2.6 Anomalie minori

- **Label quarter tax-sim**: le prime 4 righe `tax_sim_periods` (pre-13/07, periodo quarter) portano label "Q2-2026" per luglio (bug off-by-one, corretto in ba71d8d); le righe daily successive sono corrette.
- **Un FLAT marcato `filled` senza ordine né posizione** (cn-cheap BTC, 10/07 01:00): incoerenza di etichettatura del codice pre-M6.2, occorrenza singola, nessun dato da riparare.
- **`reasoning_tokens=0` per Opus thinking-only**: non è un bug — Anthropic fattura i thinking token dentro `output_tokens`; il costo registrato è corretto e la sottostima è nulla. Spot-check di conferma previsto al primo tick dello smoke (C9).
- **ChainDivergence come rumore**: ~950+ segnalazioni cumulative, tutte riconducibili alle zombie note (detection T4/ADR-0025 funzionante come safety net; nessuna divergenza inspiegata).

### 2.7 Baseline: backfill a posteriori

Le curve baseline (cash, buy&hold, naive_momentum_ema_20_50 — pre-registrate in RESEARCH §3.3) sono state calcolate per M6.1 con backfill a posteriori dai context snapshot storici (27/07, 1907 tick × 3, ADR-0036), con SL/TP valutati al close 15' (approssimazione documentata: wick intra-candle non catturati; lieve asimmetria conservativa rispetto agli SL/TP intra-tick on-chain dei modelli). In M7 le baseline saranno calcolate live dal tick 1 dallo stesso codice. Esiti M6.1 (indicativi, dataset non comparativo): cash 1000.00 · buy&hold 1017.01 · momentum 1001.21.

### 2.8 Chiusura dell'esperimento (2026-07-27)

I servizi sono stati fermati alle **14:31 UTC** (ultimo tick eseguito: 14:30, 4 run). Le 6 posizioni residue on-chain (cn-cheap BTC/ETH/SOL, cn-premium ETH/SOL, usa-cheap BTC) sono state chiuse manualmente via SDK con firma delegata — fill oid 57061132976, 57061135206, 57061136724, 57061140883, 57061142882, 57061145381. Le corrispondenti righe `positions` restano **aperte nel DB per costruzione** (bookkeeping ormai spento): 8 righe totali, cioè le 6 residue più le 2 zombie usa-premium di §2.2 (`6802457a`, `fe09ecde`). Saldi finali per wallet dopo le chiusure manuali: usa-premium **$1.007,56** · cn-cheap **$907,34** · cn-premium **$895,72** · usa-cheap **$761,36**. Nota operativa: le chiavi configurate negli agent sono **API wallet delegati** (signer ≠ master address), quindi la chiusura manuale è avvenuta con firma delegata sui master account.

## 3. Cosa cambia per M7

Tutti i difetti sopra hanno fix committati che saranno in produzione dal tick 1 dell'esperimento M7 (redeploy su sha unico, gate M6.2): ClosureReconciler (ADR-0038) · fee per-oid (8411576) · funding sign nativo (d76a2ff) · quarter label (ba71d8d) · baseline live (ADR-0036) · policy no-retry dichiarata (ADR-0037). Le precondizioni operative (credito API, wallet re-fundati, seed nuovo) sono formalizzate in M6.2-PLAN. La detection DB↔chain (ADR-0025) resta attiva come safety net con attesa di zero segnalazioni. Lo smoke M6.2 girerà sui saldi residui non uniformi di fine M6.1 (decisione esplicita di contenimento costi): il gate misura correttezza infrastrutturale, non performance; l'esperimento M7 partirà con wallet nuovi finanziati con $1.000.
