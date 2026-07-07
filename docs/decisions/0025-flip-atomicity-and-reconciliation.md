# ADR-0025: Atomicità del flip (close→open) e assenza di riconciliazione DB↔chain

**Data**: 2026-07-07
**Status**: accepted (limite noto documentato; riconciliazione tracciata come fix pre-M7, non implementata)
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
- [ ] Riconciliazione implementata (pre-M7)
- [ ] Test gating riconciliazione (flip parziale → riconvergenza)
