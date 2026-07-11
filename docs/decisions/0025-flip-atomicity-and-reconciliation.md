# ADR-0025: Atomicità del flip (close→open) e assenza di riconciliazione DB↔chain

**Data**: 2026-07-07
**Status**: accepted — **detection + alert implementata in M6.2** (2026-07-11); **auto-repair** ancora deferito (criteri sotto)
**Milestone**: emerso in M5-T14 (ricognizione post-fix-0027/0030); riconciliazione **pre-M7 BLOCCANTE** (non pre-M6)
**PRD reference**: §4.1 (decision loop / esecuzione); ADR-0027, ADR-0030 (path chiusura, di cui questo è il completamento sul flip); ADR-0022 (Opzione 2)
**Closes deferral**: none

## Contesto

Comportamento **reale** mappato in ricognizione (file:riga verificati; **non** modificato da questo
ADR). È il **Problema 3** che ADR-0030 aveva documentato come "contesto correlato", ora promosso al
proprio ADR — il completamento sul **flip** del lavoro di chiusura di ADR-0027/0030.

### Il flip esegue due ordini sequenziali on-chain, senza atomicità di exchange

Un **flip** (azione con `side` opposto a una posizione esistente) esegue on-chain **due market order
sequenziali**: prima close, poi open (`hyperliquid_client.py:494-502`). L'exchange non offre
atomicità tra i due: sono due richieste distinte.

### Lato DB i due sono atomici tra loro, ma NON con la chain

- `close_position` e `open_position` girano nella **stessa transazione**, committata insieme dal
  commit unico a fine `_execute_actions` (`decision_loop.py`) → **atomici tra loro lato DB**.
- **NON c'è atomicità chain↔DB.** Due modi di fallimento:
  1. **close on-chain riesce, open on-chain rifiutato** → l'azione è marcata `FAILED` (isolamento
     per-azione, ADR-0024) e **nulla è persistito** → **chain flat, DB con la vecchia posizione
     ancora aperta** (divergenza).
  2. **entrambi gli ordini on-chain riescono ma la persistenza DB fallisce** → **rollback**
     dell'intera transazione (close+open) → **DB coerente internamente ma divergente dalla chain
     già flippata**.

### NON esiste riconciliazione DB↔chain

Verificato (grep `reconcile`/`drift`/`sync` → nessuna logica). L'unico controllo a inizio tick,
`_check_pending_closures`, rileva **solo** i trigger SL/TP sulle posizioni che il DB crede aperte
(`check_position_closure` per symbol); **non** confronta l'insieme DB↔`clearinghouseState` e **non**
ripara le divergenze da flip parziale.

> Correzione a una convinzione precedente: **NON** esiste "riconciliazione al tick successivo".
> `_check_pending_closures` non è una riconciliazione — parte dalle posizioni che il DB crede aperte
> e verifica se sono chiuse sull'exchange; **non** fa il confronto inverso (posizioni presenti sulla
> chain e ignote al DB, o posizioni che il DB crede aperte ma la chain ha già flippato).

## Decisione

Il comportamento è **documentato come limite noto**; **nessun codice è toccato ora** (pattern
"traccia il difetto, fix in sessione dedicata", come ADR-0027).

- **Per lo smoke M6 (48h): accettabile.** Lo smoke serve proprio a stressare la pipeline e far
  emergere queste divergenze; un flip parziale è **raro** (richiede il rifiuto dell'open **dopo** un
  close riuscito) e una divergenza sarebbe **rilevabile a posteriori** confrontando dataset e chain.
  M6 **non** è bloccato da questo.
- **Per M7 (esperimento 4 settimane, dati per la tesi): la riconciliazione va implementata.** Una
  divergenza silenziosa DB↔chain **inquinerebbe il dataset**, che è il risultato scientifico. Quindi
  la riconciliazione è un **fix pre-M7 BLOCCANTE**, tracciato qui e **NON implementato ora**.

### Direzione di fix tracciata (non implementata)

A inizio tick, confrontare l'**insieme delle posizioni che il DB crede aperte** con lo
`clearinghouseState` reale; su divergenza, **riconciliare**: registrare la chiusura mancante
(posizione aperta nel DB ma assente sulla chain) oppure **segnalare l'anomalia** (posizione sulla
chain non riflessa nel DB). Da progettare in una **sessione dedicata pre-M7**.

## Implementazione (2026-07-11, M6.2): detection + alert (NO auto-repair)

La direzione tracciata sopra è ora implementata come **rilevazione + allerta**, senza
auto-riparazione (scelta deliberata per M6.2):

- `src/aiat/orchestration/chain_reconciliation.py` — `detect_chain_divergences(db_open, chain_open)`
  puro. **Hyperliquid fa netting per coin** (al più UNA posizione on-chain per symbol), mentre il
  DB può avere **più righe open per lo stesso symbol** (una riga chiusa on-chain ma mai chiusa nel
  DB = *zombie*). Quindi il confronto **aggrega le righe DB per symbol** (somma con segno: LONG +,
  SHORT −) e la confronta con l'unica posizione chain — **MAI riga-per-riga** (un dict per-symbol
  scarterebbe silenziosamente lo zombie). Categorie:
  - `zombie_row` — il DB ha size open che la chain non ha (chain flat, oppure size chain < somma DB
    sullo stesso lato): una o più righe DB sono stale;
  - `missing_row` — la chain ha una posizione per un symbol di cui il DB non ha **alcuna** riga open;
  - `size_mismatch` — entrambi presenti ma le size sommate divergono oltre tolleranza in modo non
    riconducibile a un over-count del DB (chain più grande della somma DB, o flip di lato).
  Ogni divergenza include, **per la riparazione manuale**, i `db_positions` (`position_id` + side +
  size di ciascuna riga) e il `delta` (Σ size DB con segno − size chain).
- `DecisionLoop._reconcile_chain_state(session, portfolio_state)` — invocato **a inizio tick, dopo il
  fetch del portfolio e PRIMA della decisione**. Su divergenza: **una riga `errors`
  `error_kind='ChainDivergence'`** (tutte le divergenze in `context`, con `position_id`+`delta`) +
  `logger.warning`; poi il tick **prosegue** (best-effort, non blocca né aborta mai). Nessuna
  scrittura correttiva.

### Evidenza empirica (cn-premium, 2026-07-11)

Caso reale che ha guidato la correzione del confronto: il wallet **cn-premium** aveva on-chain
**una** posizione BTC LONG (size `0.00425`, entry `62690.2`) ma nel DB **due** righe open — entry
`63403` (07-10) e `62690.2` (07-11): la prima è uno **zombie** (chiusa on-chain, mai chiusa nel DB).
Un confronto riga-per-riga (o un dict per-symbol) avrebbe mascherato lo zombie; il confronto
**netted** rileva `zombie_row` con `delta=0.01` (somma DB `0.01425` − chain `0.00425`) e riporta
**entrambi** i `position_id`. Questo caso è un test di gating (unit + e2e).

### Causa radice (T4b): SL fired tra i tick + reopen stesso symbol nello stesso tick

Ricostruzione completa del perché lo zombie è nato — un **bug di ordinamento/keying** in
`_check_pending_closures`, non solo una divergenza da flip:

Timeline (cn-premium, BTC, 2026-07-11 UTC):
- **01:06** — lo **stop-loss** della posizione `5b3c555e-…` (entry `63403`) **scatta on-chain**
  (fill `oid=56298713468`, `px=62500`, `closedPnl=-5.72`). Sull'exchange BTC va flat.
- **01:15 (tick)** — al fetch iniziale la chain mostra BTC flat; il modello **decide LONG BTC** e
  `_execute_actions` (step 8) **riapre** BTC (`oid=56301522722`) creando una **nuova** riga
  posizione. Ora BTC è di nuovo presente on-chain (`szi != 0`).
- **stesso tick, step 9** — `_check_pending_closures` itera le posizioni che il DB crede aperte
  (`list_open_for_model` → **entrambe** le righe BTC: lo zombie + la nuova) e per ciascuna chiama
  `check_position_closure("BTC")`. Ma `check_position_closure` corto-circuita su
  **`szi != 0`** (`hyperliquid_client.py:786`) **prima** di ispezionare i fill di chiusura: poiché
  la riapertura ha rimesso BTC on-chain, ritorna `None` («ancora aperta») per **entrambe** le righe.
  Il fill di SL (che è in `user_fills`) non viene mai guardato.

Risultato: l'ordine SL resta `status='triggered'` nel DB, la posizione `5b3c555e-…` **non viene mai
chiusa** → **zombie**. Due difetti concorrenti:
1. **Ordine**: `_check_pending_closures` (step 9) gira **dopo** `_execute_actions` (step 8), quindi
   la riapertura precede la rilevazione della chiusura.
2. **Keying per-symbol + short-circuit `szi`**: la rilevazione è per coin e si ferma appena la coin
   è presente on-chain, quindi una riapertura stesso-tick nasconde la chiusura precedente e non sa
   distinguere quale delle due righe DB si riferisca alla posizione chiusa.

**Mitigazione attuale (M6.2)**: la riconciliazione netted (sopra) **rileva** lo zombie risultante
(`zombie_row`, con `position_id` + `delta`) a inizio del tick successivo → il dataset è protetto
(righe divergenti identificabili/filtrabili). Test di gating: `test_misses_close_when_symbol_reopened_same_tick`
(caratterizza il short-circuit) + i test e2e a due righe (la rilevazione lo cattura).

**Fix della causa radice — DEFERITO (post-M6.2), opzioni tracciate**:
- **(A) Riordino**: eseguire la rilevazione delle chiusure **prima** di `_execute_actions`, così l'SL
  è registrato prima della riapertura. Cambia l'ordine di PRD §4.1 (step 9 → prima di step 8);
  non altera la decisione del modello (presa a step 5 sul portfolio di step 2), ma va valutato per
  effetti collaterali.
- **(B) Rilevazione a livello posizione**: `check_position_closure` dovrebbe cercare il fill di
  chiusura relativo alla **specifica** posizione (per finestra temporale `> opened_at` e/o match
  `oid` del trigger) invece di corto-circuitare sulla presenza della coin — così una riapertura non
  maschera la chiusura precedente. Più corretto ma richiede stato (oid dei trigger) e interagisce con
  l'attribuzione SL/TP (ADR-0030).
Entrambe le opzioni sono **loop surgery** sul path di chiusura (stessa famiglia di rischio di
ADR-0027/0030), quindi deferite con i criteri sotto.

### Perché detection-only per M6.2 (auto-repair deferito)

- L'auto-repair (registrare la chiusura mancante / riconciliare la size) **muta lo stato delle
  posizioni**: è esattamente il tipo di scrittura che ha generato i bug di ADR-0027/0030. Farlo bene
  richiede attribuire la causa (chiusura persa vs flip parziale vs liquidazione) e riconciliare fee e
  funding — un refactor più ampio e rischioso.
- Per M6.2/smoke, **sapere** che c'è divergenza (riga `errors` interrogabile) è sufficiente a
  proteggere il dataset: le run/posizioni divergenti sono identificabili e filtrabili a posteriori.
- La rilevazione non ha effetti collaterali → rischio nullo di introdurre nuove divergenze.

### Criteri per abilitare l'auto-repair (post-M6.2)

1. Frequenza/tipologia delle divergenze osservate nello smoke (dalle righe `ChainDivergence`) note.
2. Regola di riconciliazione decisa per ciascun `kind` — in particolare `missing_on_chain` →
   registrare la chiusura con quale `close_reason`/prezzo/fee? — coerente con ADR-0030/ADR-0032.
3. Test di riconvergenza (flip parziale → auto-repair → DB e chain riconvergono) verdi.

## Conseguenze

### Impatto
- **M6 (smoke 48h): NON bloccante.** Flip parziale raro + divergenza rilevabile a posteriori; lo
  smoke è esattamente il contesto in cui farla emergere.
- **M7 (esperimento): BLOCCANTE.** Senza riconciliazione una divergenza silenziosa corromperebbe il
  dataset. Va risolto prima di M7.

### Note
- Il flip è **raro** nel comportamento osservato finora: i 4 agent in M5-T14 **non** hanno fatto flip.

### Rischio
- **Flip parziale → divergenza non compensata** tra DB e chain, non rilevata in tempo reale (solo a
  posteriori finché la riconciliazione non è implementata).

## Alternative considerate

*(per la riconciliazione, da valutare nel fix futuro pre-M7)*

### Alternativa A: riconciliazione a inizio tick DB↔`clearinghouseState`
- Pro: semplice, non richiede atomicità dall'exchange; ripara qualunque divergenza (flip parziale,
  chiusura persa) alla granularità del tick.
- **Preferita.**

### Alternativa B: atomicità transazionale chain↔DB con compensazione on-chain
- Contro: l'exchange **non** offre atomicità nativa; la compensazione (rollback dell'ordine on-chain
  già riuscito) è **complessa** e a sua volta può fallire.
- Scartata (per ora) perché: sposta il problema senza eliminarlo (la compensazione non è atomica).

### Alternativa C: rendere il flip un **singolo** ordine che inverte la posizione
- Pro: eliminerebbe la finestra di non-atomicità (un solo ordine).
- Contro: dipende dal supporto SDK/exchange (un ordine che attraversa lo zero), **da verificare**.
- Scartata (per ora) perché: fattibilità non confermata sul SDK Hyperliquid.

## Test gating (per il fix pre-M7)

- Un **flip con open rifiutato dopo close riuscito** → la riconciliazione a inizio tick (successivo)
  **rileva e registra** la divergenza; DB e chain **riconvergono**.

## Propagazione

- [x] Limite documentato in questo ADR (ricognizione post-fix-0027/0030, 2026-07-07)
- [x] Riconciliazione **detection + alert** implementata (M6.2, 2026-07-11) —
  `chain_reconciliation.py` + `DecisionLoop._reconcile_chain_state` → riga `errors` `ChainDivergence`
- [x] Test gating detection (unit `detect_chain_divergences` netted incl. caso reale cn-premium a
  2 righe + e2e `_reconcile_chain_state` logga `ChainDivergence` e il tick prosegue)
- [x] **Causa radice T4b documentata** (SL fired + reopen stesso-tick) + test
  `test_misses_close_when_symbol_reopened_same_tick`
- [ ] **Fix causa radice T4b** (riordino step 9↔8 **oppure** rilevazione a livello posizione) —
  deferito post-M6.2 (loop surgery, criteri sotto)
- [ ] **Auto-repair** (riconciliazione correttiva delle divergenze) — deferito post-M6.2, criteri sopra
- [ ] Test gating auto-repair / fix T4b (SL+reopen stesso tick → nessuno zombie; flip → riconvergenza)
