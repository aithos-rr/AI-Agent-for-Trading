# ADR-0035: Repair one-shot delle zombie positions M6.1

**Data**: 2026-07-24
**Status**: accepted
**Milestone**: M6.2 (pre-M7)
**PRD reference**: §3.2.4 (`positions`), §3.2.6 (`fee_events`/`funding_events`), §3.2.7 (`outcomes`), §4.2 (OutcomeResolver)
**Closes deferral**: none

## Contesto

La detection T4 (vocabolario ADR-0034 + riconciliazione chain↔DB ADR-0025) ha confermato
**5 righe `positions` corrotte** nell'esperimento smoke M6.1
`55555555-5555-5555-5555-555555555555`. Tutti i valori sotto sono stati verificati **on-chain**
via `userFills` sull'API Hyperliquid **testnet** (inv #9). Due cause radice:

- **T4b** (ADR-0025): uno SL/TP trigger è scattato *tra* due tick e una riapertura nello
  stesso tick ha corto-circuitato `check_position_closure` sullo `szi != 0`
  (`hyperliquid_client.py`), lasciando in DB una zombie aperta — o applicando a essa un
  exit/fee di una chiusura successiva.
- **Morte dell'agente `usa-premium`**: credito API esaurito dal **2026-07-19 ~01:30 UTC**, per
  cui i fill TP on-chain successivi non sono mai stati registrati (la riga DB è rimasta aperta).

Il **fix root-cause di T4b è un goal separato**: questo ADR e lo script associato riparano
**solo i dati storici** dell'esperimento 5555…, senza toccare alcun codice runtime
(orchestrator/agent).

Due forme di corruzione:

- **CORREZIONE** (casi 1-2): la riga è *chiusa con la chiusura sbagliata* — un `model_close`
  successivo ha mis-applicato exit e fee alla zombie, fabbricando un PnL positivo inesistente.
- **CHIUSURA** (casi 3-5): la riga è *ancora aperta* in DB ma è stata chiusa on-chain da un
  trigger TP.

## Decisione

Un unico script one-shot `scripts/repair_zombie_positions.py`, con le stesse garanzie dei
repair precedenti (`backfill_fees.py`, `flip_funding_signs.py`):

- **Dry-run di default**: stampa (via `structlog`, come gli altri script — inv T201) un blocco
  `repair_plan` per riga con il diff `old→new` e gli insert previsti, e **non scrive nulla**.
  Con `--apply` committa.
- **Una sola transazione**. Fase di planning **read-only** (nessun write), poi apply. Se `--apply`
  incontra anche un solo caso `ERROR` (riga corrotta ma con una dipendenza mancante), fa
  **rollback totale** e solleva `RepairAbort`: niente scritto.
- **Idempotenza tramite pre-state assertion per riga**: se lo stato non combacia con quello
  corrotto atteso (già riparata o divergente) la riga è **SKIP** con messaggio; le altre
  procedono. Un secondo `--apply` produce quindi **5 SKIP** e zero scritture.
- **Driver DB**: `asyncpg` + SQLAlchemy async da `AIAT_DATABASE_URL`. Scelto per coerenza con
  il progetto (tutti i modelli ORM sono async-mapped, come `backfill_fees.py`) e per **riusare i
  modelli ORM e `OutcomeResolver`** senza reimplementare né SQL a mano né la logica di outcome.
- **Riuso `OutcomeResolver`**: i campi outcome derivati (`pnl_net_fee_usd`,
  `pnl_net_fee_funding_usd`, `was_profitable_net`, `horizon_met`) sono calcolati da
  `OutcomeResolver.resolve_position` — identico al path runtime `close_position`. Il floor a
  minuti di `holding_duration_min` è stato **estratto in una funzione pura condivisa**
  (`aiat.execution.outcome_resolver.holding_duration_min`) per non reimplementare la formula
  (`positions.py` mantiene il gemello inline; il runtime **non** è modificato).

### Le 5 convenzioni

1. **exit multi-fill** = VWAP dei fill dello stesso `oid` (già consolidato nei valori sotto).
2. **`closing_run_id`** per chiusure senza run di bookkeeping = il **primo run dello stesso model
   con `run_started_at > closed_at` reale**, *qualsiasi status*. Una zombie chiusa dopo la morte
   dell'agente si àncora al primo run `failed` che segue (di qui «qualsiasi status»).
3. **SL/TP autonomo** → `closing_action_id = NULL` (CHECK `chk_position_closed_consistency`,
   ADR-0030): nessuna `decision_action` del modello ha causato la chiusura.
4. **`realized_pnl_usd`** = `closedPnl` on-chain (gross, fee esclusa), somma dei fill.
5. **Funding mis-attribuito** dopo la chiusura reale (righe con `created_at > closed_at` reale)
   → **riassegnato** (`funding_events.position_id`) alla posizione successiva dello stesso
   model/symbol (minimo `opened_at > closed_at`).

Le CHIUSURE (casi 3-5) inseriscono il `taker_close` `FeeEvent` sull'**ordine trigger TP scattato**
(`fee_events.order_id` NOT NULL) — stesso link di `close_position` (ADR-0032) — con
`run_id = closing_run` (conv. 2). L'outcome inserito prende `confidence`/`time_horizon` dalla
**opening action** e `funding = Σ` righe della posizione. Nelle CORREZIONI `confidence` e
`time_horizon_min` dell'outcome **non** sono toccati (vengono dall'apertura), così come
`pnl_net_fee_funding_tax_sim_usd` (writer separato, ADR-0033).

### Tabella dei 5 casi (fonti on-chain)

| # | Tipo | `position_id` | model | sym | close oid | `closed_at` (UTC) | exit | `realized_pnl` (gross) | fee close |
|---|------|---------------|-------|-----|-----------|-------------------|------|------------------------|-----------|
| 1 | CORREZIONE | `3e6acfe5-…c20597` | usa-premium | BTC | SL `56309051125` | 2026-07-13 13:43:40.756 | 62280 | −8.784576 | 0.253915 (da 0.259336) |
| 2 | CORREZIONE | `5b3c555e-…774f6ad` | cn-premium | BTC | SL `56298713468` | 2026-07-11 01:06:37.003 | 62500 | −5.72502 | 0.178312 (da 0.242666) |
| 3 | CHIUSURA | `da4823d5-…b44633b` | usa-premium | BTC | TP `56597441225` | 2026-07-17 17:48:05.168 | 64056.3 (VWAP 8) | 7.90145 | 0.196297 |
| 4 | CHIUSURA | `c1624ba0-…08c32340` | usa-premium | BTC | TP `56623016995` | 2026-07-20 18:01:55.471 | 65567.1 (VWAP 6) | 10.56115 | 0.200632 |
| 5 | CHIUSURA | `710fe90d-…f676e8686`* | usa-premium | SOL | TP `56650748691` | 2026-07-19 02:07:15.715 | 75.962 | 6.34226 | 0.159292 |

\* `710fe90d-8a34-4432-b56b-af5c25e78686`.

Caso 1, funding: le righe `funding_events` con `created_at >` 2026-07-13 13:43:40.756
appartengono alla posizione successiva `usa-premium` BTC long 0.00674 aperta 2026-07-13 13:45
(open oid `56418645763`) e vengono riassegnate (conv. 5); il dry-run mostra n righe e totale
spostato. Caso 2: funding atteso 0 (verifica comunque righe post-chiusura da riassegnare).

## Conseguenze

### Positive
- Il dataset M6.1 riflette la verità on-chain: PnL fabbricati (+12.31, +2.16) corretti in perdite
  reali (−8.78, −5.73); 3 zombie aperte chiuse con il PnL/fee/funding corretti.
- Nessuna migration, nessun cambio di runtime. Riuso di `OutcomeResolver` → net PnL identico al
  path di chiusura reale.

### Negative
- Valori hard-coded nello script (verificati manualmente on-chain una tantum). L'idempotenza li
  protegge da riesecuzioni, ma non da un DB già divergente in altro modo (in quel caso: SKIP).
- **Scope della riassegnazione funding (conv. 5)**: lo script sposta `funding_events.position_id`
  alla posizione successiva ma **non ricalcola l'outcome di quella posizione destinataria**. Se la
  destinataria è già chiusa con un outcome (probabile: lo smoke è arrivato al 20/07), il suo
  `sum_funding_usd`/`pnl_net_fee_funding_usd` resta calcolato senza le righe spostate. È un residuo
  fuori dai 5 casi enumerati: va ricalcolato separatamente (il dry-run stampa il
  `target_position_id`, così l'operatore sa quale outcome ricomputare). Le CHIUSURE (casi 3-5)
  sommano invece **tutte** le righe funding della posizione senza boundary/riassegnazione —
  asimmetria **deliberata** (specchia il path runtime `close_position`) e verificata on-chain per
  questi 3 casi.

### Neutre (trade-off accettati)
- `holding_duration_min` è duplicato: la funzione pura condivisa (nuova) + il gemello inline in
  `positions.py` (non modificato per non toccare il runtime). Formula identica.
- Lo script è **one-shot per l'esperimento 5555… e NON va rilanciato sul dataset M7**
  (`EXPERIMENT_ID` è una costante; i pre-state sono specifici di queste 5 righe).

## Alternative considerate

### Alternativa A: SQL manuale ad-hoc
- Pro: nessun codice.
- Contro: nessuna idempotenza, nessun dry-run, ricalcolo net PnL a mano (divergenza dal runtime).
- Scartata perché: un repair di dati finanziari deve essere ripetibile, reversibile in dry-run e
  usare la **stessa** logica di outcome del path reale.

### Alternativa B: sync `psycopg` invece di `asyncpg`
- Pro: leggermente più semplice per uno script one-shot.
- Contro: non riuserebbe i modelli ORM async né `OutcomeResolver` senza duplicare mapping/logica.
- Scartata perché: la coerenza col progetto (async ovunque) e il riuso pesano più della brevità.

## Test gating

`tests/integration/test_repair_zombie_positions.py` (pytest-postgresql): semina i 5 stati
corrotti + scaffold FK e verifica (a) dry-run no-op, (b) apply = valori target esatti + campi
derivati (net PnL, `closing_run_id` conv. 2, funding riassegnato conv. 5), (c) rilancio → 5 SKIP
senza scritture né duplicati, (d) pre-state divergente → SKIP solo di quella riga. In più: guard
inv #9 (non-testnet) e rollback su `ERROR`. Il helper `holding_duration_min` ha unit test in
`tests/unit/execution/test_outcome_resolver.py`.

## Propagazione

- [x] Script `scripts/repair_zombie_positions.py`
- [x] Funzione pura condivisa `aiat.execution.outcome_resolver.holding_duration_min`
- [x] Test in `tests/integration/test_repair_zombie_positions.py` + unit del helper
- [x] Indice ADR aggiornato (`docs/decisions/README.md`)
- [ ] Fix root-cause T4b (`check_position_closure` short-circuit su `szi!=0`) — **goal separato** (ADR-0025)
