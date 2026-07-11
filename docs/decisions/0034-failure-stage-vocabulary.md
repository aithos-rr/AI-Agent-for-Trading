# ADR-0034: Vocabolario chiuso di `runs.failure_stage` + semantica `errors`

**Data**: 2026-07-11
**Status**: accepted
**Milestone**: M6.2 (pre-M7)
**PRD reference**: §8.2 (gerarchia eccezioni LLM), §3.2.3 (`runs`), §3.2.9 (`errors`)
**Closes deferral**: none (formalizza il mapping introdotto dal fix finding D, commit `9ad318a`)

## Contesto

Prima del fix finding D, il generic handler di `run_once` marcava `runs.failure_stage='error'`
per **ogni** fallimento non-timeout e logicava l'eccezione solo su structlog, senza scrivere su
`errors`: nello smoke M6 esteso 36 run `failed` hanno lasciato **8** righe `errors` (tutte
`MissedTick`). Il fix `9ad318a` ha (i) persistito ogni fallimento su `errors` e (ii) introdotto un
mapping per-classe di `failure_stage`. Quel mapping è però rimasto **implicito nel codice**
(`decision_loop._failure_stage_for`): serve un vocabolario **esplicito e chiuso** perché
`failure_stage` è un asse di analisi del dataset (quante run falliscono per auth vs rate-limit vs
parsing) e perché le docstring in `aiat/llm/exceptions.py` promettevano etichette (`llm_auth`,
`llm_parse`) mai formalizzate.

## Decisione

Il vocabolario di `runs.failure_stage` è **chiuso** ai seguenti valori:

| valore | quando | classe eccezione |
|--------|--------|------------------|
| `NULL` | run non fallita (`success`/`partial`/`running`) | — |
| `timeout` | timeout dell'intero tick (`asyncio` `TimeoutError` sul `wait_for` di `run_once`) → `runs.status='timeout'` | `TimeoutError` builtin |
| `llm_auth` | credenziali LLM invalide/insufficienti | `LLMAuthError` |
| `llm_rate` | rate limit provider | `LLMRateLimitError` |
| `llm_parse` | structured output + fallback freetext entrambi non parsabili | `LLMUnrecoverableError` |
| `error` | **default** — qualunque altro `Exception` non classificato | (tutto il resto) |

Enforcement: **in codice** via `decision_loop._failure_stage_for(exc)` (mappa le classi sopra) +
il ramo `except TimeoutError` per `timeout`. **NON** viene aggiunto un CHECK su `runs.failure_stage`
(vedi Alternative): il vincolo è mantenuto dal codice, non dal DB.

**Semantica `errors`** correlata:
- Ogni run `failed`/`timeout` scrive **una riga `errors`** con `error_kind = tipo eccezione`
  (`type(exc).__name__`), `error_message`, `stack_trace`, e FK `run_id`/`experiment_id`/`model_id`
  (finding D, `_record_failure`).
- `error_kind='MissedTick'` è **semanticamente distinto**: non è un fallimento del tick ma
  l'assenza del `context_snapshot` dopo i retry → `runs.status='missed'`, **nessun** `failure_stage`
  (non si crea nemmeno la riga `runs` in quel caso). Scritto da `_execute_tick` via `log_error`.
- **Residuo noto**: `LLMTimeoutError` (eccezione custom, **non** sottoclasse del `TimeoutError`
  builtin) raggiunge il generic handler e ricade nel default `error` (non `timeout`). Documentato in
  `_failure_stage_for`; restringerlo cambierebbe il control-flow swallow-vs-reraise del ramo timeout.

## Conseguenze

### Positive
- `failure_stage` diventa un asse di analisi affidabile e documentato (auth/rate/parse distinguibili).
- Allinea le docstring di `aiat/llm/exceptions.py` (`llm_auth`/`llm_parse`) alla realtà del codice.
- Nessuna migration.

### Negative
- Vocabolario enforced solo lato codice: un valore fuori-vocabolario è possibile a livello DB (ma
  nessun path lo scrive; il solo writer è `_failure_stage_for` + il ramo timeout).

### Neutre (trade-off accettati)
- `LLMTimeoutError → error` (non `timeout`) è un compromesso deliberato di scope (finding D).

## Alternative considerate

### Alternativa A: CHECK constraint su `runs.failure_stage`
- Pro: enforcement a livello DB.
- Contro: **migration** su tabella esistente (con dati M6 che contengono già `error`/`timeout`); il
  set di valori potrebbe ancora evolvere (es. futuro `llm_timeout`), rendendo il CHECK un costo
  ricorrente di migration.
- Scartata perché: «migration solo se inevitabile»; il vocabolario è piccolo e scritto da un solo
  punto di codice, quindi l'enforcement applicativo è sufficiente e più flessibile.

## Test gating

`tests/e2e/test_decision_loop_error_persist.py` (finding D, già in `9ad318a`): per ciascuna classe
(`LLMAuthError`→`llm_auth`, `LLMRateLimitError`→`llm_rate`, `LLMUnrecoverableError`→`llm_parse`,
generica→`error`) e per il timeout (`→timeout`) verifica riga `errors` + `runs.failure_stage`.

## Propagazione

- [x] Mapping implementato in `decision_loop._failure_stage_for` (`9ad318a`)
- [x] Vocabolario documentato qui (ADR-0034)
- [ ] (Opzionale) `llm_timeout` dedicato per `LLMTimeoutError` se si rivedrà il control-flow del ramo
- [ ] Aggiornare le docstring di `aiat/llm/exceptions.py` con un rimando a questo ADR
