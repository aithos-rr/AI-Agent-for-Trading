# ADR-0031: Funding ledger — job orchestrator 8h da HL `userFunding`

**Data**: 2026-07-11
**Status**: accepted
**Milestone**: M6.2 (pre-M7)
**PRD reference**: §4.2 (Flusso 2 — Funding & Outcome Resolution)
**Closes deferral**: none (chiude il gap di implementazione «finding B» dello smoke M6 esteso)

## Contesto

Lo smoke M6 esteso (4 giorni, ~374 run/agent) ha lasciato `funding_events` **a 0 righe**
nonostante posizioni perp aperte per giorni: su Hyperliquid il funding matura ogni ora, quindi
0 righe è impossibile se non per un gap di implementazione. L'analisi (finding B) ha confermato:
la tabella `funding_events` e lo schema esistono (migration `001`), `PositionsRepository.close_position`
**legge** già `sum(funding_amount_usd)` per popolare `outcomes.sum_funding_usd`, ma **nessun
componente scrive** `funding_events` — nessun writer, nessuna chiamata HL funding, nessun job
schedulato. Il PRD §4.2 lo richiede esplicitamente («Job parallelo APScheduler che gira ogni 8 ore
… per ogni position OPEN: fetch funding rate da HL, crea funding_event row»); non è tra i deferral
D1-D5. Conseguenza scientifica: `pnl_net_fee_funding_usd` sistematicamente sovrastimato (funding
mancante), il che inquinerebbe il dataset di M7.

## Decisione

Implementare il **funding ledger** come job APScheduler 8h nel **context-orchestrator**:

- **`HLPublicInfoClient.user_funding_history(user, start_time_ms, end_time_ms=None)`**
  (`src/aiat/context/collectors/onchain.py`): POST read-only `{"type":"userFunding",...}` al `/info`
  pubblico HL. Nessuna private key (l'indirizzo wallet è pubblico). Rispecchia
  `Info.user_funding_history` dell'SDK.
- **`FundingReconciler`** (`src/aiat/orchestration/funding_reconciler.py`): apre una sessione DB,
  carica le posizioni OPEN dell'esperimento, mappa `model_id → wallet_address` dalla tabella
  `models`, interroga `userFunding` per wallet, e per ogni pagamento orario crea un `FundingEvent`
  **contro la posizione aperta** di quel `(model, coin)` che era già aperta al tempo del funding.
- **Leggere i pagamenti reali, non ricalcolarli**: `funding_amount_usd`/`funding_rate` vengono dal
  delta USDC e dal rate che HL ha effettivamente applicato (venue-accurate), non da `notional × rate`.
- **Idempotenza per chiave naturale `(position_id, funding_period_end)`** via *check-then-insert*.
  NON viene aggiunta una UNIQUE constraint (niente migration): il job è single-instance
  (`max_instances=1`), quindi non c'è race di scrittura. Lookback di 25h per sovrapporre le finestre
  di run consecutivi (i duplicati vengono deduplicati) e tollerare un run mancato.
- **Wiring**: `build_scheduler_for_orchestrator` accetta un `funding_job` opzionale (CronTrigger
  `hour="0,8,16"`); `__main__._build_funding_job` lo costruisce. `outcomes.sum_funding_usd` già
  somma la tabella alla chiusura → nessun altro cablaggio.

`funding_period_end` = timestamp del pagamento HL; `funding_period_start` = `end − 1h` (funding orario,
soddisfa `chk_funding_period_end_gt_start`).

## Conseguenze

### Positive
- `funding_events` finalmente popolato → `pnl_net_fee_funding_usd` corretto per M7.
- Ledger venue-accurate (legge i pagamenti reali), non una stima.
- Idempotente e resiliente a run mancati/sovrapposti senza migration.
- Fetch read-only sul wallet pubblico: nessun segreto nell'orchestrator (least privilege intatto).

### Negative
- L'orchestrator ora legge la tabella `models` (wallet_address) e scrive `funding_events` per tutti
  i modelli: prima non toccava `models`. È una scrittura ledger cross-model (come i `context_snapshots`
  condivisi), non una query agent (inv #1 riguarda le query agent).
- `check-then-insert` fa una SELECT per record; volume M6.2 trascurabile.

### Neutre (trade-off accettati)
- **Shape della response `userFunding` è un'ASSUNZIONE** da validare su testnet live
  (`{"time", "delta": {"type":"funding","coin","usdc","fundingRate"}}`), come il pattern «ASSUMPTION
  (validate M4-T08)» già presente nel client. Il parser è difensivo (scarta record malformati/coin
  non supportate) e il test usa la shape reale documentata.
- Job nell'orchestrator (come da istruzione/PRD) invece che per-agent: l'orchestrator ha già
  session factory + client HL read-only; il funding è un fatto per-wallet, non market context (inv #13
  non è violato — quello riguarda il fetch di sorgenti esterne durante il tick di decisione).

## Alternative considerate

### Alternativa A: job per-agent (ogni agent riconcilia il proprio wallet)
- Pro: isolamento per modello naturale; l'agent ha già `RealHyperliquidClient`.
- Contro: il PRD colloca il job nell'orchestrator; duplicherebbe la logica in 4 processi; l'agent
  fetcherebbe una sorgente esterna fuori dal context_snapshot (attrito con lo spirito di inv #13).
- Scartata perché: il PRD §4.2 è esplicito («job parallelo» lato orchestrator) e l'orchestrator ha
  già gli strumenti.

### Alternativa B: UNIQUE `(position_id, funding_period_end)` + `ON CONFLICT DO NOTHING`
- Pro: idempotenza a livello DB, robusta anche a concorrenza.
- Contro: richiede una migration (constraint su tabella esistente).
- Scartata perché: il vincolo «migration solo se inevitabile» + job single-instance rendono il
  check-then-insert applicativo sufficiente e senza migration.

## Test gating

- `tests/e2e/test_funding_reconciler.py`: contro Postgres reale, con fake funding source che ritorna
  la shape reale HL → verifica creazione `FundingEvent` (importo/rate/periodo) e **idempotenza**
  (secondo run: 0 creati, skip). Tripwire: pre-fix non esisteva alcun writer → il test non passa.
- `tests/unit/orchestration/test_funding_reconciler.py`: parser puro (shape reale + casi di scarto).
- `tests/unit/context/test_onchain.py`: `user_funding_history` (payload, non-200, body non-lista).

## Propagazione

- [x] Implementato in `src/aiat/orchestration/funding_reconciler.py` + `HLPublicInfoClient.user_funding_history`
- [x] Wiring scheduler (`build_scheduler_for_orchestrator(funding_job=...)`) + `__main__._build_funding_job`
- [x] Test e2e + unit
- [x] Osservabilità drift shape: `reconcile` conta i record funding-typed non parsati
  (`parse_failed`) e logga un warning — il silent-skip (rischio classe finding-A) è visibile
- [x] **SEGNO di `usdc` — VALIDATO (2026-07-12) e CHIUSO** (vedi sezione sotto)
- [ ] Aggiornare `PRD_V2.md` §4.2 con riferimento a questo ADR

## Convenzione di segno del funding — validazione e fix (2026-07-12)

L'item di validazione che questo ADR aveva aperto è **chiuso**. Confronto empirico del CSV
`funding-history` HL (wallet `usa-premium`) con `funding_events`: **la convenzione HL di `usdc` è
`+ = incassato` / `- = pagato`** (match esatto sui valori, es. BTC 12-13/07 `-0.00727` HL ↔ `-0.0072`
DB pre-fix).

**Bug del reconciler**: la prima versione salvava `funding_amount_usd = usdc` **verbatim** (segno
HL). Ma la convenzione canonica del DB è quella del **PRD §3.2.6** — `funding_amount_usd signed:
+ = paghi, - = ricevi` — **opposta** a HL, e correttamente assunta da TUTTI i consumer:
`PositionsRepository.close_position` e `OutcomeResolver` (`pnl_net_fee_funding = pnl_net_fee −
Σ funding_amount_usd`) e la tax-sim (`net = gross − fees − funding`). Con lo storage verbatim, un
funding **incassato** (HL positivo) veniva **sottratto** → PnL peggiorato: segno invertito.

**Decisione (convenzione B, scelta dell'utente)**: mantenere **una sola convenzione end-to-end =
quella del PRD §3.2.6** (`+ = paid`), invariata per tutti i consumer e la tax-sim; **negare al
momento dell'ingest** nel reconciler (`funding_amount_usd = -delta.usdc`). Blast radius minimo (una
riga nel writer nuovo), nessuna modifica ai consumer/PRD/loro test. Il parser resta fedele a HL
(ritorna `usdc` grezzo); la conversione avviene alla scrittura del `FundingEvent`.
Alternativa scartata (A): tenere lo storage = segno HL e girare i 3 consumer + tax-sim + PRD §3.2.6
(deviazione da PRD frozen, blast radius ampio) — più fedele alla fonte ma non necessaria.

**Riparazione dati (una-tantum)**: le righe `funding_events` già scritte dal reconciler pre-fix hanno
tutte il segno invertito → `scripts/flip_funding_signs.py` (dry-run di default, `--execute` per
scrivere: `UPDATE funding_events SET funding_amount_usd = -funding_amount_usd`). **Ordine**: deploy
del fix → poi flip (se si flippa prima, le righe scritte dal reconciler vecchio nel frattempo
tornano sbagliate). **NON idempotente** (un secondo run re-inverte). Gli `outcomes` dello smoke M6
avevano `sum_funding_usd = 0` (tabella vuota all'epoca) → nessun ricalcolo necessario.

**Test**: `test_stores_prd_canonical_sign_with_real_hl_values` usa i segni reali del CSV — long che
PAGA (rate `+0.0000125` → HL `usdc<0` → DB `+0.0072`) e long che INCASSA (rate `<0` → HL `usdc>0`
→ DB `-0.0072`) — e morde se la negazione viene rimossa o invertita.

### Diagnostica ~~aperta~~ CHIUSA: riga `funding_events` 23:00 12/07 (rate `-0.00029142`, amount `+0.0834`)

**CHIUSA (2026-07-24) — FALSO ALLARME.** La riga non "mancava" dal CSV: l'export HL
`funding-history` è in **ora locale CEST (UTC+2)**, mentre il DB registra in **UTC**. La riga DB
delle 23:00 UTC corrisponde alla riga CSV dell'01:00 CEST del giorno dopo — offset di 2h del tutto
innocuo (causa (a) sotto: granularità/fuso dell'endpoint, non un errore di ledger né di
attribuzione). Segno e importo erano già coerenti. **Ledger pulito**, nessuna azione. La
descrizione originale dell'indagine resta sotto per contesto storico.

Segnalata come non corrispondente ad alcuna riga CSV di quell'ora per quel wallet. Internamente la
riga è **coerente** (rate `<0` → long incassa → `usdc>0` → pre-fix DB `+0.0834`), quindi NON è un
errore di segno. Cause plausibili da verificare **con i dati** (non risolvibile dal codice):
(a) granularità/endpoint diversi (`userFunding` vs l'export CSV `funding-history`);
(b) attribuzione a una posizione dello stesso coin ma finestra diversa (`_match_position` sceglie
`max(opened_at ≤ funding_time)` — corretto se gli `opened_at` sono giusti, ma uno **zombie**
(vedi ADR-0025) può spostare l'attribuzione tra righe DB dello stesso symbol).
Query diagnostica (da WSL, il codice non ha accesso al dato di produzione):

```sql
SELECT fe.funding_period_end, fe.funding_rate, fe.funding_amount_usd,
       fe.position_id, p.symbol, p.opened_at, p.closed_at
FROM funding_events fe JOIN positions p ON p.id = fe.position_id
WHERE fe.model_id = :model_id
  AND fe.funding_period_end >= '2026-07-12 22:00:00+00'
  AND fe.funding_period_end <  '2026-07-13 00:00:00+00'
ORDER BY fe.funding_period_end;
```

Se `position_id` punta a una posizione con `opened_at` posteriore all'ora del funding o a uno zombie,
è un problema di attribuzione (ADR-0025), non del ledger funding.
