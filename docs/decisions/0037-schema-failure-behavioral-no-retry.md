# ADR-0037: Schema-failure come dato comportamentale (no retry)

**Data**: 2026-07-24
**Status**: accepted
**Milestone**: M6.2 (pre-M7)
**PRD reference**: §8.2 (gerarchia eccezioni LLM), §4.1 (tick loop), RESEARCH §7 (criteri gate / RQ1-RQ2)
**Closes deferral**: none

## Contesto

Una quota non trascurabile di tick (~10% per `usa-cheap`) produce una response LLM che **non
supera la validazione Pydantic** dello schema `TradeDecision`, anche dopo l'unico fallback freetext
pre-registrato (ADR-0028/0029). La scelta se **ri-provare / ri-promptare** questi fallimenti impatta
sia il control-flow del tick sia la validità scientifica: un retry che insiste finché il modello
produce JSON valido **maschererebbe** la vera compliance-allo-schema al primo colpo, introducendo un
trattamento sperimentale **asimmetrico** tra modelli (il modello peggiore riceverebbe più tentativi)
— un confound diretto per RQ2/RQ3. Serve ratificare la policy e verificare che il fallimento sia
**pulito** (nessun effetto collaterale) così che la run fallita sia un dato osservabile e non un bug.

## Decisione

1. **Schema-failure = dato comportamentale, nessun retry a livello di tick.** Una response che
   fallisce la validazione su **entrambi** i tentativi (structured primario + unico fallback
   freetext) solleva `LLMUnrecoverableError`; il tick termina con `runs.status='failed'` e
   `runs.failure_stage='llm_parse'` (vocabolario ADR-0034). Il tick **non** viene rigiocato né
   ri-schedulato, e non riceve ulteriori re-prompt oltre l'unico fallback già pre-registrato. La
   schema-compliance al primo colpo è una **variabile comportamentale misurata**, non un errore da
   nascondere.

2. **Il fallback freetext (ADR-0028) RESTA per M7 — decisione ratificata.** È **pre-registrato**
   (ADR-0028/0029): rimuoverlo devierebbe dalla pre-registrazione. È **un solo** tentativo correttivo
   in-invoke, applicato **uniformemente ai 4 modelli** (quindi simmetrico, non un confound), e la sua
   occorrenza è tracciata per-modello da `fallback_used`. Metriche derivate:
   - `fallback_used=True` → il primario ha fallito lo schema, recuperato dal fallback;
   - run `LLMUnrecoverableError`/`failure_stage='llm_parse'` → schema-failure irrecuperabile (entrambi
     i tentativi falliti). Il ~10% di `usa-cheap` è questo tasso irrecuperabile.

   **Variabile misurata (definizione operativa):** poiché il fallback resta parte del protocollo, la
   variabile comportamentale è la **"capacità di produrre output utilizzabile entro il protocollo
   pre-registrato (structured output + un fallback freetext)"** — NON la schema-compliance al primo
   colpo in senso stretto. Un tick conta come schema-failure solo se fallisce **entrambi** i tentativi
   del protocollo; `fallback_used` misura separatamente il ricorso al fallback (proxy della compliance
   al primo colpo), ma il criterio di fattibilità (§7 / punto 3) usa il tasso irrecuperabile.

3. **Conseguenza sui criteri gate (RESEARCH §7 / RQ1).**
   - **C1 per-modello ≥ 95% ESCLUDENDO gli schema-failure** dal denominatore (i tick falliti di
     validazione non contano come tick "profittevoli/non-profittevoli": sono esclusi).
   - **Soglia dedicata ≥ 85% INCLUSIVA** per `usa-cheap` (che porta il ~10% di schema-failure): la
     compliance-allo-schema entra così esplicitamente nel giudizio di fattibilità di quel modello.

4. **Retry di rete/5xx restano legittimi e simmetrici.** I client langchain (`ChatOpenAI`,
   `ChatAnthropic`, `OpenAICompatibleClient`) usano il retry di trasporto **built-in** dell'SDK
   (default `max_retries=2` su 5xx/429/errori di connessione) prima di sollevare. Questi sono
   uniformi tra i 4 modelli e riguardano il **trasporto**, non la validazione: la policy no-retry di
   questo ADR si applica **solo** ai fallimenti di validazione Pydantic, mai a rate-limit/5xx/timeout
   (che hanno le loro eccezioni dedicate: `LLMRateLimitError`/`LLMAuthError`/`LLMTimeoutError`, §8.2).

## Prerequisito verificato (read-only): il fallimento è pulito

Ispezione di `structured.invoke_structured` + `decision_loop._execute_tick`
(`src/aiat/orchestration/decision_loop.py`):

- **Nessun ordine parziale.** L'invocazione LLM è lo **step [5]** (`decision_loop.py:309`);
  l'esecuzione ordini (`_execute_actions`) è lo **step [8]** (`:336`). Un `LLMUnrecoverableError`
  sollevato allo step [5] impedisce per costruzione di raggiungere gli step [7] (persist decisione)
  e [8] (ordini) → **nessuna** riga `decisions`/`decision_actions`/`orders`/`positions`/`fee_events`.
- **Nessuna scrittura spuria.** Le uniche scritture pre-invoke sono la riga `runs` (poi marcata
  `failed`) e l'`account_snapshot` pre-decisione — entrambe bookkeeping legittimo del tick, non
  spurie.
- **Fallimento persistito.** Il generic handler (`run_once`) chiama `_record_failure(run_id, exc,
  FAILED, 'llm_parse')` → una riga `errors` (`error_kind='LLMUnrecoverableError'`, finding D) +
  `runs.status='failed'`/`failure_stage='llm_parse'`.
- **Limite noto (non un effetto collaterale):** il costo dei tentativi falliti **non** è messo a
  ledger (il `cost_event` è persistito allo step [7], non raggiunto) — sotto-conteggio minore del
  costo API sui tick di schema-failure, già documentato in `stats_handler` (M2-T12). Non tocca
  posizioni/PnL.

## Conseguenze

### Positive
- Rimuove un confound sperimentale (retry asimmetrico) e rende la schema-compliance una metrica di
  prima classe (RQ2/RQ3).
- Il fallimento è pulito e osservabile: `failure_stage='llm_parse'` è un asse di analisi affidabile.

### Negative
- `usa-cheap` può risultare penalizzato dalla soglia inclusiva ≥85% — ma è l'informazione voluta.
- Costo API dei tentativi falliti non ledgerizzato (limite pre-esistente, non introdotto qui).

### Neutre (trade-off accettati)
- L'unico fallback freetext resta attivo (ADR-0028) **e resta anche per M7 (ratificato)**: è
  simmetrico tra i modelli e pre-registrato, quindi ammesso e parte del protocollo misurato. La
  variabile è la capacità di produrre output utilizzabile *entro il protocollo* (structured + un
  fallback), non la compliance stretta al primo colpo (che resta osservabile via `fallback_used`).

## Alternative considerate

### A: retry/re-prompt finché lo schema è valido
- Pro: meno run fallite; più dati di trading.
- Contro: trattamento asimmetrico tra modelli (confound), maschera la compliance reale. **Scartata.**

### B: rimuovere il fallback freetext per M7
- Pro: misura la compliance strettamente al primo colpo.
- Contro: devia dalla pre-registrazione (ADR-0028/0029); il fallback è simmetrico tra i modelli, il
  ~10% è già il tasso post-fallback e `fallback_used` copre comunque la compliance al primo colpo.
- **Scartata (ratificata):** il fallback RESTA per M7; la variabile misurata è ridefinita come
  "output utilizzabile entro il protocollo pre-registrato" (vedi Decisione punto 2).

## Test gating

Copertura esistente sufficiente: `tests/e2e/test_decision_loop_error_persist.py`
(`LLMUnrecoverableError → failure_stage='llm_parse'` + riga `errors`, no scritture di esecuzione) e
`tests/unit/llm/test_structured.py` (fallback singolo su parse-failure, poi `LLMUnrecoverableError`).
Nessun nuovo test: questo ADR ratifica una policy e i criteri gate, non cambia il codice.

## Propagazione

- [x] Policy + criteri gate documentati qui (ADR-0037)
- [x] Indice ADR aggiornato (`docs/decisions/README.md`)
- [ ] Aggiornare `RESEARCH_DESIGN.md` §7 con C1 ≥95% (escl. schema-failure) + soglia ≥85% incl. `usa-cheap`
- [x] Fallback freetext per M7: **RESTA** (ratificato); variabile misurata ridefinita come "output
      utilizzabile entro il protocollo pre-registrato (structured + un fallback)"
