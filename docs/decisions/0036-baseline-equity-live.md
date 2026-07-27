# ADR-0036: Baseline equity live (curve non-LLM per tick)

**Data**: 2026-07-24
**Status**: accepted
**Milestone**: M6.2 (pre-M7)
**PRD reference**: §3.2.8 (`baseline_configs`/`baseline_equity_snapshots`), §4.4 (Baseline Computation), RESEARCH §3.3 (baseline pre-registrati)
**Closes deferral**: none

## Contesto

La dashboard è pronta a renderizzare le curve equity dei 3 baseline non-LLM da
`baseline_equity_snapshots`, ma la tabella non è mai stata popolata: `seed_experiment.py` (A10)
scrive le 3 righe `baseline_configs` (`buy_and_hold`, `cash`, `naive_momentum_ema_20_50`) ma
**nessun processo calcola gli snapshot equity**. Il buco copre lo smoke M6.1.

Il PRD §4.4 prevedeva un calcolo **a posteriori** (`scripts/compute_baselines.py` a fine
esperimento). Decisione ratificata dall'utente: i baseline sono calcolati **LIVE** dall'orchestrator
a ogni tick (deviazione dal PRD §4.4 → questo ADR). Il calcolo a posteriori resta disponibile come
catch-up/backfill (buco M6.1 + recovery).

I 3 baseline sono **pre-registrati e vincolanti** in RESEARCH §3.3 (parametri fissati prima del run
per prevenire l'ottimizzazione a posteriori). Le definizioni operative qui devono combaciare con il
seed e con ciò che la dashboard assume (lo schema tabella + i 3 `baseline_name` sono l'intero
contratto: la dashboard vive fuori da questo repo e legge la tabella direttamente).

Dati disponibili per tick nel `context_snapshots.context_json` (`ContextBundle`): per simbolo
`price_usd`, `ema_20`, `ema_50` (EMA su candele 15m, già calcolate dal `TechnicalCollector`) e
`onchain.funding_rate_8h`. **Nessun high/low intra-tick** è persistito — solo il close a 15m.

## Decisione

### Definizioni operative dei 3 baseline (RESEARCH §3.3)

- **cash**: equity costante $1000, PnL 0, costo 0.
- **buy_and_hold**: allocazione equal-weight $1000/3 su ciascuno di BTC/ETH/SOL al **primo tick**
  dell'esperimento, unità frazionarie tenute a precisione piena, **no rebalancing, no fee**;
  mark-to-market sul `price_usd` di ogni tick. (Il seed conferma il basket equal-weight BTC/ETH/SOL,
  non BTC-only.)
- **naive_momentum_ema_20_50**: 3 sotto-strategie indipendenti (BTC/ETH/SOL), ognuna con book
  $1000/3. Segnale = incrocio EMA(20)×EMA(50) su 15m (entrambe dal snapshot). **LONG** su up-cross,
  **SHORT** su down-cross; size = **20% dell'equity del book come margine**, leverage **3×**
  (notional = 0.6×equity, allineato al guardrail LLM — `sizing.py`); **SL 3% / TP 6%**; **uscita
  anticipata su cross inverso** che ha **precedenza** su SL/TP ("anche prima di SL/TP", §3.3); **una
  posizione per simbolo** (no overlap: un nuovo segnale *stessa direzione* mentre la posizione è
  aperta è ignorato). Il cross si rileva confrontando le EMA del tick precedente (portate nello
  stato) con quelle correnti. **Interpretazione dichiarata**: un cross inverso *chiude e capovolge*
  nello stesso tick (la stessa down-cross che chiude un LONG apre uno SHORT — coerente con "SHORT su
  down-cross"), quindi la strategia è sempre allineata allo stato EMA corrente, salvo quando è flat
  dopo un'uscita SL/TP (resta flat fino al prossimo cross). Il fill del flip/cross-inverso è al close;
  quello di SL/TP è al livello.

### Parità di costo (§3.3: "fee/funding/tax-sim applicati identicamente ai modelli LLM")

- **Fee taker** su open + close dedotta dentro `equity_usd`/`pnl_usd_cumulative` (solo momentum;
  buy&hold/cash sono fee-free per §3.3). Costante `TAKER_FEE_RATE = 0.00045` (taker perp HL, 4.5 bps):
  **unico parametro non derivabile dal snapshot**. **Validato empiricamente** sui `fee_events`/
  `userFills` dell'esperimento — `fee_usd/nozionale` su **518 righe `taker_open`** ≈ 0.000450 (il
  valore iniziale 0.00035 era errato). Dichiarato in `compute.py`.
- **Funding** da `onchain.funding_rate_8h`, accrescimento pro-rata per tick 15m (`rate/32`), segno
  §3.2.6 (LONG paga quando il rate è positivo). Dedotto dall'equity mentre la posizione è aperta.
- **Tax-sim**: NON entra nella curva equity — resta un layer di analisi separato (ADR-0033), come per
  gli `outcomes` dei modelli LLM.

### SL/TP valutati sul close (approssimazione dichiarata)

Il snapshot non porta high/low intra-tick e il path live non deve chiamare HL, quindi SL/TP sono
valutati sul **close** del tick: un breach è rilevato quando il close supera il livello e il fill è
bookato **al livello** (cross inverso e flip fillano invece al close). Conseguenze dichiarate:
- **Wick intra-candle** che rientrano entro il close **non sono catturati** (approssimazione
  conservativa).
- **Asimmetria vs i modelli LLM**: i loro SL/TP scattano **intra-tick on-chain**, la baseline solo al
  close. Da dichiarare come limite nel confronto RQ1.
- **Enhancement futuro (fuori scope ora)**: persistere high/low per tick (estendere
  `TechnicalCollector` + `ContextBundle` + migration) renderebbe SL/TP fedele live e in backfill; gli
  snapshot M6.1 storici resterebbero comunque close-only.

### Architettura

- **Modulo puro riusabile** `src/aiat/baselines/compute.py`: `compute_cash` / `compute_buy_and_hold`
  / `compute_momentum` — `(prev_raw_state, tick_market) -> BaselineResult(equity, pnl, raw_state)`,
  nessun I/O, `Decimal` ovunque (inv #12). Lo `raw_state` (JSONB) porta lo stato tra i tick (unità
  buy&hold; books + `prev_ema` momentum) a **precisione piena**; solo `equity_usd`/`pnl` sono
  quantizzati a 8dp (Numeric(20,8)) dal runner, mai lo stato (niente drift).
- **Glue DB** `src/aiat/baselines/runner.py` (`BaselineRunner`): estrae il `TickMarket` dal
  `ContextBundle`, carica lo stato precedente, calcola, persiste. Usato sia live sia dal backfill.
- **Step live**: il context-orchestrator, dopo aver persistito il snapshot
  (`__main__._orchestrator_tick`), invoca `run_live_tick` con il bundle già in mano — **nessuna
  chiamata HL aggiuntiva**. È **best-effort**: un errore del baseline NON fa fallire il tick di
  contesto (già committato) e viene loggato; il backfill recupera.
- **Backfill/catch-up** `scripts/compute_baselines.py`: rigioca la STESSA logica sui
  `context_snapshots` storici dell'esperimento in ordine di `tick_at`. Dry-run di default (scrive
  nulla), `--apply` committa in una transazione. `AIAT_DATABASE_URL` da env; `--experiment-id`
  esplicito o auto-select se ne esiste uno solo.

### Idempotenza e gestione gap

- **Idempotenza**: UNIQUE `(experiment_id, baseline_name, tick_id)`. Un tick già presente è un no-op —
  il suo `raw_state` è **portato avanti** così la sequenza resta esatta (re-run/resume). Live e
  backfill saltano i tick esistenti.
- **Gap** (MissedTick → nessun `context_snapshot`): nessuno snapshot inventato; la curva riprende al
  tick successivo disponibile, di cui lo stato è calcolato dall'ultimo snapshot baseline disponibile
  (il cross momentum confronta le EMA a cavallo del gap; il funding accresce solo per i tick
  processati — sotto-conteggio trascurabile e dichiarato). Uno snapshot malformato è trattato come gap.

## Conseguenze

### Positive
- La dashboard ha finalmente le 3 curve; RQ1 (confronto vs baseline non-LLM) diventa calcolabile.
- Una sola logica (modulo puro) alimenta live e backfill → nessuna divergenza; il buco M6.1 è
  riempibile subito col backfill.
- Fedele alla pre-registrazione §3.3 (parametri vincolati); `Decimal` ovunque; nessuna migration.

### Negative
- SL/TP close-only ≠ fill intra-tick on-chain dei modelli (approssimazione + asimmetria dichiarate).
- `TAKER_FEE_RATE` è un assunto (3.5 bps) da confermare col tier effettivo dell'esperimento.
- Il funding della baseline è modellato da `funding_rate_8h` (pro-rata), non da `userFunding` reale
  (la baseline non ha posizioni on-chain).

### Neutre (trade-off accettati)
- Deviazione dal PRD §4.4 (a-posteriori → live) ratificata; il backfill resta come recovery.
- Momentum con 3× leverage + SL 3% mantiene l'equity ampiamente > 0 (SL protegge dalla liquidazione),
  quindi il CHECK `equity_usd >= 0` non è mai violato in pratica; un'eventuale violazione fallirebbe
  loud (nessun clamp che nasconda un bug).

## Alternative considerate

### A: solo backfill a posteriori (PRD §4.4 originale)
- Pro: nessuna modifica runtime.
- Contro: la dashboard resta vuota durante il run; nessuna curva live. Scartata: decisione ratificata
  per baseline live.

### B: SL/TP fedele via re-fetch OHLC / high-low persistito
- Pro: cattura i wick intra-tick.
- Contro: chiamate HL nel path (vietate) o migration schema/context. Scartata ora, citata come
  enhancement futuro.

### C: nessun costo nella curva (gross), fee/funding/tax solo in analisi
- Pro: snapshot più semplici. Contro: la curva non sarebbe direttamente confrontabile con l'equity
  netta dei modelli. Scartata: §3.3 impone parità di costo (fee+funding in equity; tax in analisi).

## Test gating

- `tests/unit/baselines/test_compute.py`: le 3 logiche con prezzi noti a mano — cash; buy&hold
  mark-to-market + persistenza unità; momentum open LONG/SHORT, TP/SL fill-al-livello (long **e**
  short), flip su cross inverso con precedenza su SL/TP, funding (segno long paga/short incassa),
  **cross vero vs regola-di-stato** (ticks già sopra/sotto senza incrocio → nessuna apertura;
  mutation-tested), **no-overlap** (posizione non ri-aperta senza cross), **3 book indipendenti**.
- `tests/integration/test_baselines_runner.py` (pytest-postgresql): dry-run no-op; backfill scrive
  3×N con valori attesi (cash costante, buy&hold 1020 a +6%); **idempotenza** (re-run non duplica);
  **catch-up** (riempie solo i tick mancanti portando lo stato tra due run); **gap** (tick mancante →
  nessuno snapshot inventato, curva riprende) e **snapshot malformato** (saltato come gap); step live
  idempotente.

## Propagazione

- [x] Modulo puro `src/aiat/baselines/compute.py` + glue `src/aiat/baselines/runner.py`
- [x] Read helpers su `BaselineRepository` (`get_equity_snapshot`, `get_latest_equity_snapshot_before`)
- [x] Step live in `__main__._orchestrator_tick` (best-effort, dopo il snapshot)
- [x] `scripts/compute_baselines.py` (dry-run default, `--apply`)
- [x] Test unit + integrazione
- [x] Indice ADR aggiornato (`docs/decisions/README.md`)
- [ ] (Enhancement futuro) persistere high/low per tick per SL/TP intra-tick fedele
