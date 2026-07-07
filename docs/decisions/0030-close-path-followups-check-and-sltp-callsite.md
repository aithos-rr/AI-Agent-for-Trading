# ADR-0030: Follow-up del fix ADR-0027 — CHECK troppo restrittivo per chiusure senza azione (SL/TP) e call-site `_check_pending_closures` non aggiornato

**Data**: 2026-07-06
**Status**: accepted — **Problemi 1+2 fixati: 2026-07-07 (commit `b65e833`)**; Problema 3 (atomicità flip + riconciliazione) **aperto → ADR-0025**
**Milestone**: M5-T14 (scoperto in review avversariale post-fix-0027), follow-up **pre-M6 BLOCCANTE** (P1+P2 chiusi 2026-07-07)
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

Questo ADR **traccia** i tre problemi. Il fix di **Problemi 1 e 2** era pianificato per una
**sessione dedicata pre-M6**: **è stato implementato il 2026-07-07** (commit `b65e833`) — vedi la
sezione «Implementazione (2026-07-07)» in fondo. La diagnosi dei tre problemi qui sopra resta la
**storia com'era osservata** (2026-07-06), non riscritta a posteriori. Il **Problema 3** (atomicità
flip + assenza di riconciliazione) **resta aperto** ed è demandato ad **ADR-0025**.

Interventi previsti (stato dopo il fix del 2026-07-07):

- **[fatto]** Rivedere `chk_position_closed_consistency` (revisione della 004, **non ancora pushata**)
  per condizionare il requisito `closing_action_id IS NOT NULL` a `close_reason='model_close'`,
  ammettendolo NULL per `stop_loss`/`take_profit`/`liquidated`.
- **[fatto]** Aggiornare il call-site `_check_pending_closures` alla firma a 5 argomenti, coerentemente
  con la revisione del CHECK (per una chiusura SL/TP: `closing_action_id` / `close_order` = `None`).
- **[fatto]** Aggiungere un **test del path SL/TP con repository reale** (non mockato) che esercita la
  firma vera e la persistenza della chiusura SL/TP.
- **[aperto → ADR-0025]** Scrivere **ADR-0025** (atomicità flip) e decidere se/come mitigare la
  divergenza chain↔DB e l'assenza di riconciliazione (Problema 3).

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
- **Follow-up runtime (fix `fix(lifecycle)` 2026-07-07)**: con la **004 diventata head** e applicata,
  la costante `EXPECTED_ALEMBIC_VERSION` in `src/aiat/orchestration/lifecycle.py:22` era rimasta a
  `"003"` → `_check_db_connectivity_and_schema` avrebbe fatto **fallire al boot tutti i 5 servizi** su
  un DB a 004 (mismatch atteso `003` vs `004`). Bumpata a `"004"`. **Regola ricorrente**: ogni
  migration che sposta l'head richiede il **bump di questa costante**. Confermato **hardcode**, non
  lettura dinamica dell'head: il servizio *dichiara* la schema version attesa — la lettura dinamica
  maschererebbe il drift codice↔schema e svuoterebbe il check. **Una riga qui basta** (nessun ADR
  dedicato; ADR-0021 non è pertinente — riguarda il seed). Nello stesso fix corretto anche il
  messaggio d'errore stale di **A5** (`lifecycle.py:114`): `register_prompt_template.py` →
  `scripts/seed_experiment.py` (coerente con ADR-0021: seed unico).

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
- [x] Sessione fix pre-M6: revisione CHECK + call-site `_check_pending_closures` + test SL/TP reale
  (Problemi 1+2, commit `b65e833`, 2026-07-07 — vedi «Implementazione (2026-07-07)»)
- [ ] ADR-0025 (atomicità flip + riconciliazione, Problema 3) da scrivere — **resta aperto**
- [x] Follow-up runtime (fix `fix(lifecycle)`, 2026-07-07): `EXPECTED_ALEMBIC_VERSION` `003`→`004`
  (004 head applicata) + messaggio A5 → `scripts/seed_experiment.py` — vedi «Note»
- [x] Aggiornare ADR-0027: la parte **SL/TP** del checkbox "Coordinamento con path SL/TP e flip"
  (§Propagazione di ADR-0027) **si chiude qui** (b65e833); la parte **flip** resta ad ADR-0025. (Lo
  spunto del checkbox nel file di ADR-0027 è una modifica separata a quel documento.)
- [ ] Sessione audit-completa: limiti deferiti **(iii)** marcare `filled` la riga `orders` del
  trigger scattato + **(iv)** riconciliare la fee di chiusura nell'`Outcome`

## Implementazione (2026-07-07)

Fix di **Problemi 1 e 2** implementato e committato (commit `b65e833`, 2026-07-07). Suite verde
(693 passed, 1 skipped e2e testnet, a parte la failure preesistente e non correlata in
`test_settings`). Il **Problema 3 resta aperto** → ADR-0025.

**Scelta: Strada 1 raffinata** — `closing_action_id` e `close_order` resi **OPZIONALI** (`None` per
le chiusure autonome), **non fabbricati**. **NON** è stata adottata la **Strada 2** (agganciare e
aggiornare a `filled` la riga `orders` del trigger scattato): richiede la disambiguazione SL-vs-TP via
`oid` e un refactor più ampio di `close_position` → **deferita alla sessione audit-completa**.

- **CHECK condizionale** — migration `004` **rivista in-place** (non ancora pushata):
  `chk_position_closed_consistency` richiede `closing_action_id IS NOT NULL` **solo** per
  `close_reason='model_close'`; lo **ammette NULL** per `stop_loss`/`take_profit`/`liquidated`. Il
  `__table_args__` del model `Position` (`src/aiat/db/models/position.py`) è allineato alla stessa
  definizione. (`'manual'` non è ammesso sul ramo «chiuso»: nessun path automatico lo produce.)
- **`close_position`** (`src/aiat/db/repositories/positions.py`): firma
  `closing_action_id: str | None`, `close_order: OrderResult | None`. Sul ramo `None` (chiusura
  autonoma SL/TP/liquidazione): **non** setta `pos.closing_action_id`, **non** inserisce la riga
  `orders` `order_kind='close'`, **non** crea il `FeeEvent` di chiusura; setta comunque i campi di
  chiusura della posizione e crea l'`Outcome` (che usa `opening_action_id`, non il closing). Il ramo
  `model_close` (entrambi valorizzati) resta invariato (bookkeeping ADR-0027).
- **Attribuzione per-lato** (`_attribute_close_reason` in
  `src/aiat/orchestration/decision_loop.py`): la **liquidazione ha priorità** (flag sul fill →
  `liquidated`, euristica non applicata); altrimenti attribuzione **per-lato** — LONG:
  `exit ≤ entry → stop_loss`, `exit > entry → take_profit`; SHORT invertito; il tie `exit == entry`
  (strutturalmente impossibile per un trigger reale) → `stop_loss` + `logger.warning`. Usa **solo
  `entry_price` + `side`** (immune alla divergenza tra i prezzi trigger memorizzati e quelli effettivi
  sul venue).
- **Call-site**: `_check_pending_closures` (path SL/TP autonomo) passa `closing_action_id=None`,
  `close_order=None` sulla firma a 5 argomenti. La review ha scoperto un **3° call-site** rotto,
  `tests/e2e/test_testnet_smoke.py` (path `model_close`): corretto passando un `closing_action_id`
  reale (azione FLAT seminata) + il `close_order` reale già prodotto nel test. **Grep esaustivo**
  (`src/`, `tests/`, `scripts/`): **10 chiamanti** di `close_position`, **tutti a 5 argomenti**.
- **Test**: attribuzione per-lato (**7 casi**: LONG SL/TP, SHORT SL/TP, liquidated-priorità, tie
  LONG/SHORT); chiusura SL/TP autonoma con **repository reale** (`closing_action_id` NULL, nessuna
  riga `close`, CHECK condizionale l'**accetta**) + teeth-test che il CHECK **rifiuta** `model_close`
  con `closing_action_id` NULL; teeth-test `model_close` di ADR-0027 **invariato**; call-site e2e con
  firma corretta; aggiornato `test_check_pending_closures_detects_closure_by_symbol` alla firma vera.

**Limiti accettati** (deferiti alla sessione audit-completa / ADR-0025):

- **(i)** l'attribuzione SL-vs-TP è **euristica per-lato**, **non** matching dell'`oid` del trigger;
- **(ii)** assume che le chiusure autonome siano **solo** trigger SL/TP o liquidazione (nessun
  intervento manuale esterno) — `close_reason='manual'` resta **irraggiungibile** e fuori dal CHECK;
- **(iii)** le righe `orders` dei trigger restano `status='triggered'` (l'exit **non** è marcato
  `filled` a livello ordine);
- **(iv)** la fee di chiusura del trigger **non entra** nell'`Outcome` (riconciliazione fee deferita).
