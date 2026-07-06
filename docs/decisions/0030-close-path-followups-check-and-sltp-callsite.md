# ADR-0030: Follow-up del fix ADR-0027 — CHECK troppo restrittivo per chiusure senza azione (SL/TP) e call-site `_check_pending_closures` non aggiornato

**Data**: 2026-07-06
**Status**: accepted (tracciamento difetto; fix in sessione dedicata pre-M6)
**Milestone**: M5-T14 (scoperto in review avversariale post-fix-0027), follow-up **pre-M6 BLOCCANTE**
**PRD reference**: §3.2.3 / §7.6 (positions/orders); §4.1 step [9] (`_check_pending_closures` / SL/TP); ADR-0027 (fix di cui questo è follow-up); ADR-0024 (per-action isolation); ADR-0025 (atomicità flip, da scrivere)
**Closes deferral**: none

## Contesto

Il fix di ADR-0027 (commit `1ec8029`, 2026-07-06) ha:

- aggiunto la persistenza della riga `orders` `order_kind='close'` + `closing_action_id`;
- reso `closing_action_id` **obbligatorio** nel ramo "chiuso" del CHECK
  `chk_position_closed_consistency` (migration 004);
- cambiato la firma di `close_position` (nuovi parametri obbligatori `closing_action_id`,
  `close_order`).

Una **review avversariale successiva** ha rivelato tre problemi — due introdotti/non-considerati
dal fix stesso, uno preesistente correlato.

### Problema 1 — CHECK 004 troppo restrittivo per chiusure senza azione del modello

Il nuovo CHECK richiede `closing_action_id IS NOT NULL` per **ogni** posizione chiusa. Ma le
chiusure innescate da **trigger SL/TP** o da **liquidazione** avvengono sull'exchange **senza una
`decision_action` del modello** che le causi (è l'exchange a chiudere, non una scelta FLAT). Per
queste chiusure **non esiste un `closing_action_id` semanticamente valido**.

Conseguenza: il CHECK 004 **rifiuterebbe la persistenza di una chiusura SL/TP legittima** (o
costringerebbe a inventare un `closing_action_id` falso, inquinando la tracciabilità). Il fix
0027(c) ha considerato **solo il path FLAT** (chiusura con azione), non i path senza azione.

Il CHECK va rivisto: **ammettere `closing_action_id` NULL** quando
`close_reason IN ('stop_loss','take_profit','liquidated')`, **richiederlo solo** quando
`close_reason='model_close'` (chiusura FLAT esplicita).

> Nota: `close_reason` ammette `'manual','stop_loss','take_profit','liquidated','model_close'`
> — vedi `chk_position_close_reason`.

### Problema 2 — call-site `_check_pending_closures` non aggiornato (REGRESSIONE)

`decision_loop.py:451` chiama `close_position(str(position.id), closure_info, run_id)` con **3
argomenti**, ma il fix 0027 ha reso la firma a **5** (`closing_action_id`, `close_order` ora
obbligatori). Il call-site del flip (`:416`) è stato aggiornato, questo del path SL/TP **no**.

Conseguenza: in produzione, **ogni chiusura innescata da trigger SL/TP solleverebbe
`TypeError: missing 2 required positional arguments`**. Regressione introdotta dal fix 0027.

Non intercettata dai test perché l'unico test che entra nel ramo
(`test_check_pending_closures_detects_closure_by_symbol`, `test_decision_loop.py:1122`) **mocka
`close_position`**, mascherando la firma reale.

Questo è **esattamente il "coordinamento con il path SL/TP"** che `ADR-0027:84` aveva flaggato come
`[ ]` da fare e che è rimasto scoperto. Legato al Problema 1: sistemare questo call-site richiede
**prima decidere cosa passare come `closing_action_id`** per una chiusura SL/TP (→ dipende dalla
revisione del CHECK).

### Problema 3 (contesto correlato, preesistente) — flip non atomico + nessuna riconciliazione

Mappato in review (comportamento **reale**, non modificato da noi):

- Il **flip** (side opposto a posizione esistente) esegue **due market order sequenziali on-chain**:
  close poi open (`hyperliquid_client.py:494-502`), senza atomicità di exchange.
- Lato DB, `close_position` e `open_position` girano nella **stessa transazione** e sono committati
  insieme (`decision_loop.py:258`) → atomici tra loro lato DB.
- **NON c'è atomicità chain↔DB**: se il close on-chain riesce e l'open è rifiutato, l'azione è
  marcata `FAILED` e **nulla è persistito** → chain flat, DB con vecchia posizione ancora aperta
  (divergenza). Se entrambi gli ordini on-chain riescono ma la persistenza DB fallisce, l'intera
  transazione fa **rollback** → DB coerente internamente ma divergente dalla chain già flippata.
- **NON esiste riconciliazione DB↔chain**: verificato (grep `reconcile`/`drift`/`sync` → nessuna
  logica). L'unico controllo a inizio tick, `_check_pending_closures` (`:436-451`), rileva solo
  trigger SL/TP sulle posizioni che il DB crede aperte; **non** confronta l'insieme
  DB↔`clearinghouseState` e **non** ripara divergenze da flip parziale. (Correzione a una convinzione
  precedente: **NON** c'è "riconciliazione al tick successivo".)

Questo Problema 3 è **materia dell'ADR-0025** (atomicità flip), che verrà scritto nella sessione di
fix insieme alla revisione del CHECK — qui è documentato come **contesto correlato** perché condivide
la radice (path di **modifica di posizioni esistenti**) e perché il fix del CHECK (Problema 1) e la
semantica flip vanno decisi insieme.

## Decisione

Questo ADR **traccia** i tre problemi; **nessun codice/schema è toccato ora**. Il fix è pianificato
per una **sessione dedicata pre-M6**. Interventi previsti (fuori scope di questo documento):

- Rivedere `chk_position_closed_consistency` (nuova migration, **o** revisione della 004 che **non è
  ancora pushata**) per condizionare il requisito `closing_action_id IS NOT NULL` a
  `close_reason='model_close'`, ammettendolo NULL per `stop_loss`/`take_profit`/`liquidated`.
- Aggiornare il call-site `_check_pending_closures:451` alla firma a 5 argomenti, coerentemente con
  la revisione del CHECK (cosa passare come `closing_action_id` / `close_order` per una chiusura
  SL/TP).
- Aggiungere un **test del path SL/TP con repository reale** (non mockato) che esercita la firma vera
  e la persistenza della chiusura SL/TP.
- Scrivere **ADR-0025** (atomicità flip) e decidere se/come mitigare la divergenza chain↔DB e
  l'assenza di riconciliazione.

## Conseguenze

### Impatto
- **BLOCCA M6**. SL/TP è il **meccanismo di uscita primario** (le posizioni si chiudono da trigger
  tra i tick, più spesso del FLAT esplicito). Con il call-site rotto (Problema 2), il **primo trigger
  SL/TP in M6 crasherebbe**; con il CHECK troppo restrittivo (Problema 1), anche sistemato il
  call-site, la persistenza della chiusura SL/TP verrebbe **rifiutata**. Entrambi vanno risolti prima
  di M6.

### Note
- **Nota di processo**: la migration 004 **non è pushata** → può essere **rivista** invece di
  accumulare una 005. Da valutare nella sessione di fix.
- **Lezione**: cambiare una firma richiede di grepare **tutti** i call-site (`close_position` aveva
  due chiamanti: flip `:416` aggiornato, SL/TP `:451` dimenticato). I test che **mockano** il metodo
  **mascherano le rotture di firma** — servono test che esercitano la firma reale.

## Alternative considerate

*(per la revisione del CHECK, da valutare nel fix)*

### Alternativa A: CHECK condizionale su `close_reason` (ammette NULL per SL/TP)
- Pro: semanticamente corretto (la chiusura SL/TP non ha un'azione del modello).
- **Preferita.**

### Alternativa B: inventare un `closing_action_id` sintetico per SL/TP
- Contro: inquina la tracciabilità (l'azione non esiste).
- Scartata perché: fabbricherebbe un riferimento decisione→chiusura inesistente.

### Alternativa C: rimuovere il requisito `closing_action_id` dal CHECK
- Contro: perderebbe la difesa per il path FLAT.
- Scartata perché: sarebbe una **regressione su 0027** (il buco che 0027 aveva chiuso).

## Test gating (per la sessione di fix)

- Chiusura SL/TP con repository **reale** (non mock): persiste correttamente con `closing_action_id`
  NULL e `close_reason` in (`stop_loss`/`take_profit`); il CHECK rivisto la **accetta**.
- Chiusura FLAT (`model_close`): continua a **RICHIEDERE** `closing_action_id` (il teeth-test di 0027
  resta valido).
- Il call-site `:451` **non solleva** `TypeError` su trigger reale.

## Propagazione

- [x] Difetti tracciati in questo ADR (review avversariale post-fix-0027, 2026-07-06)
- [ ] Indicizzare in `docs/decisions/README.md`
- [ ] Sessione fix pre-M6: revisione CHECK + call-site `:451` + test SL/TP reale
- [ ] ADR-0025 (atomicità flip) da scrivere nella stessa sessione
- [ ] Aggiornare ADR-0027: il checkbox "[ ] Coordinamento con path SL/TP e flip" si chiude qui
