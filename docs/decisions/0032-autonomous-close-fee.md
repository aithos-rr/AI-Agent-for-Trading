# ADR-0032: Fee di chiusura autonoma SL/TP persistita

**Data**: 2026-07-11
**Status**: accepted
**Milestone**: M6.2 (pre-M7)
**PRD reference**: §4.2 / §7.6 (bookkeeping chiusure)
**Closes deferral**: chiude il limite **(iv)** di ADR-0030 per SL/TP (liquidazione resta deferita, ADR-0025)

## Contesto

ADR-0030 elencava tra i limiti accettati: **(iv)** «la fee di chiusura del trigger **non entra**
nell'`Outcome` (riconciliazione fee deferita)». Nel frattempo il fix finding A (commit `51a8e45`)
ha reso `RealHyperliquidClient.check_position_closure` capace di estrarre la fee dei fill di
chiusura e di valorizzarla su `PositionClosureInfo.fee_usd`. Ma quel valore **non veniva ancora
persistito**: nel path di chiusura autonoma (`close_position` con `closing_action_id=None` e
`close_order=None`) `positions.py` saltava del tutto la creazione del `fee_event`. Quindi le
chiusure SL/TP autonome (15 nello smoke M6) avevano `sum_fees_usd` senza la fee di chiusura,
sottostimando i costi e sovrastimando il PnL netto.

Vincolo tecnico: `fee_events.order_id` è **NOT NULL** (FK → `orders.id`). Per creare un
`FeeEvent` di chiusura serve un ordine a cui agganciarlo. Nel path autonomo non esiste una riga
`orders` di kind `close` (non abbiamo inviato un ordine di chiusura), ma **esistono** le righe
`orders` dei trigger SL/TP create all'apertura (`order_kind` `stop_loss`/`take_profit`,
`status='triggered'`, linkate via `decision_action_id = positions.opening_action_id`).

## Decisione

Nel ramo autonomo di `PositionsRepository.close_position`, quando `close_order is None` **e**
`closure.fee_usd is not None` **e** `close_reason ∈ {stop_loss, take_profit}`:

1. Individua la riga `orders` del trigger scattato per `order_kind` corrispondente al
   `close_reason` (query per `decision_action_id = opening_action_id` + `order_kind`).
2. Crea un `FeeEvent` con `fee_type='taker_close'` (via `_fee_type`), `fee_usd=closure.fee_usd`,
   `order_id` = quel trigger, `occurred_at=closed_at`, **prima** della `select sum(fee_usd)`, così
   la fee entra in `sum_fees_usd` → `pnl_net_fee`.
3. Se il trigger non si trova (non dovrebbe accadere), logga un warning e prosegue senza fee.

**Liquidazione**: la fee resta **deferita** (ADR-0025). Una liquidazione non è un fill SL/TP e non
ha una riga `orders` nostra a cui linkarla senza fabbricare un'attribuzione falsa; persistirla
richiederebbe uno schema/attribuzione dedicati (audit-complete session).

Non viene aggiunta né modificata alcuna constraint (nessuna migration): `chk_position_closed_consistency`
è sulla riga `positions`, non toccata; `_fee_type(stop_loss|take_profit)` ritorna già `taker_close`.

## Conseguenze

### Positive
- `sum_fees_usd`/`pnl_net_fee`/`pnl_net_fee_funding` corretti per le chiusure SL/TP autonome.
- La fee catturata al confine (finding A) ora ha una destinazione persistente.
- Fee agganciata all'ordine trigger reale che l'ha generata (audit trail coerente).

### Negative
- La riga `orders` del trigger resta `status='triggered'` (non marcata `filled`): resta il limite
  ADR-0025 (marcatura del trigger scattato). La fee però è ora corretta.
- Per la liquidazione la fee resta fuori dall'`Outcome` (deferita).

### Neutre (trade-off accettati)
- Attribuzione SL-vs-TP ancora **per-lato** (ADR-0030 (i)), non per-`oid`: si aggancia la fee al
  trigger del lato attribuito. Coerente con come `_check_pending_closures` determina il `close_reason`.

## Alternative considerate

### Alternativa A: rendere `fee_events.order_id` nullable + fee di chiusura non linkata
- Pro: gestirebbe anche la liquidazione.
- Contro: migration su tabella esistente; perde il legame fee↔ordine (audit più debole).
- Scartata perché: «migration solo se inevitabile» e il legame all'ordine trigger è disponibile e utile.

### Alternativa B: sintetizzare una riga `orders` `close` per la chiusura autonoma
- Pro: simmetria con il path model_close.
- Contro: fabbricherebbe un ordine mai inviato (dati falsi); confligge con lo spirito di ADR-0030.
- Scartata perché: non abbiamo emesso un ordine di chiusura; il trigger esistente è la verità.

## Test gating

`tests/integration/test_db_repositories_positions.py`:
- `test_close_position_autonomous_persists_sltp_fee` (STOP_LOSS/TAKE_PROFIT, non mockato): la fee
  di chiusura è un `taker_close` `FeeEvent` linkato all'ordine trigger, `sum_fees_usd=0.70`
  (0.30 entry + 0.40 close). Tripwire: pre-fix sarebbe 0.30.
- `test_close_position_autonomous_liquidation_fee_deferred`: liquidazione con `fee_usd` valorizzato
  → nessun `FeeEvent` di chiusura, `sum_fees_usd=0.30` (solo entry).

## NOTA 2026-07-24 — bug fee SL/TP gonfie (filtro `user_fills`) corretto

`RealHyperliquidClient.check_position_closure` (la fonte di `PositionClosureInfo.fee_usd` per le
chiusure autonome, "path T2") sommava **tutti** i fill di chiusura recenti del wallet per quel coin
(`user_fills` è una finestra rolling wallet-wide), non i soli fill dell'ordine di chiusura scattato.
Su coin ri-tradati molte volte questo gonfiava la taker fee di 10–50× (rate fee/nozionale 0.005–0.023
vs il tier 0.00045). **Fix**: si restringe ai fill del **solo `oid` della chiusura più recente**
(i partial condividono un `oid`); realized PnL ed exit derivano dallo stesso insieme. Test:
`test_fee_and_pnl_use_only_the_latest_close_order_oid` (fixture `user_fills` con fill di più
ordini/symbol mescolati; falliva col codice pre-fix). ~10 righe su 506 `taker_close` erano affette;
la massa (496) era corretta.

**Dati storici NON riparati** (dataset M6.1 archiviato) — vanno in nota metodologica. Righe affette
note (fee attuale gonfia → fee attesa ≈ nozionale×0.00045), da enumerare via query
`fee_events`⋈`positions` (`WHERE fee_type='taker_close' AND close_reason IN ('stop_loss',
'take_profit') AND fee_usd/(size_units*exit_price) > 0.001`):

| model | symbol | close | size | exit | fee attuale | fee attesa |
|-------|--------|-------|------|------|-------------|-----------|
| usa-cheap | ETH | take_profit | 0.1853 | 1828.6 | 8.247966 | ≈0.152 |
| cn-premium | BTC | stop_loss | 0.00619 | 65187 | 5.361142 | ≈0.182 |

(elenco completo = output della query sopra sul DB di prod; il codice non ha accesso al dato.)

## Propagazione

- [x] Implementato in `PositionsRepository.close_position` (ramo autonomo)
- [x] Test integrazione reali (SL/TP + liquidazione deferita)
- [x] Aggiornato ADR-0030 (limite (iv) chiuso per SL/TP)
- [x] Fix filtro `user_fills` per-oid in `check_position_closure` (NOTA 2026-07-24) + test
- [ ] Marcare `filled` la riga `orders` del trigger scattato (resta ADR-0025)
- [ ] Fee di liquidazione (resta deferita, ADR-0025)
