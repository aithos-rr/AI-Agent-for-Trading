# ADR-0029: Structured output provider-aware; il confine ADR-0008 si materializza su accesso diretto

**Data**: 2026-07-06
**Status**: accepted (ratificato da Riccardo, 2026-07-06)
**Milestone**: M5-T14 (scoperta empirica), vincolante per M6/M7
**PRD reference**: §8.2 (`invoke_structured`); ADR-0008 (routing dual-mode, confine dev/direct); ADR-0023 (provider-aware sampling, gemello); ADR-0028 (OpenAI fallback variance); RESEARCH §7
**Closes deferral**: none

## Contesto

`invoke_structured` (`structured.py`) usava `method="json_schema"` **hard-coded**, verificato
funzionante via OpenRouter (dev gateway, ADR-0008) per i modelli OpenAI-compatible. Una nota nel
codice prevedeva che i provider diretti avrebbero potuto richiedere un *"provider-aware method —
to be verified at M6"*.

M5-T14 (smoke con accesso **DIRETTO** ai provider, non OpenRouter) ha materializzato empiricamente
quel confine: **OpenRouter normalizzava le differenze, l'accesso diretto no**. Ogni provider ha un
meccanismo di structured-output diverso, scoperto tramite errori **HTTP 400 reali** catturati al
primo invoke. Questo ADR documenta che **"OpenAI-compatible" non implica comportamento identico**.

Questo ADR è il gemello concettuale di ADR-0023: ADR-0023 ha reso i client provider-aware sul
**sampling**; questo li rende provider-aware sullo **structured-output**. Stesso metodo (un
confine reale alla volta, osservato non presunto), stessa milestone di scoperta (M5-T14).

## I quattro comportamenti osservati

- **OpenAI (`gpt-4.1-mini`)**: `json_schema` nativo funziona su accesso diretto. Varianza residua
  ~25% (ricorso al fallback freetext nonostante `temperature=0` + `seed`) — documentata in
  ADR-0028. `fallback_used` è la metrica per-modello. Nessun adattamento di `method` necessario.
- **Anthropic (Opus 4.8)**: structured-output via **tool-use nativo**, compatibile col thinking
  mode. Rifiuta `temperature` (ADR-0023). Nessun adattamento di `method` necessario.
- **Qwen (`qwen3.7-max`, DashScope-intl)**: `json_schema` rifiutato con:

  ```
  HTTP 400 — InvalidParameter: 'messages' must contain the word 'json'
  ```

  (vincolo del gateway Alibaba). Passando a `function_calling`, secondo 400:

  ```
  HTTP 400 — tool_choice does not support being set to required or object in thinking mode
  ```

  `qwen3.7-max` è **thinking di default**, e il thinking rompe lo structured-output in
  **ENTRAMBE** le strade. Risolto: `structured_method="function_calling"` + disattivazione
  thinking via `extra_body={"enable_thinking": False}`.
- **DeepSeek (`deepseek-v4-flash`, api.deepseek.com)**: `json_schema` rifiutato con:

  ```
  HTTP 400 — This response_format type is unavailable now
  ```

  (DeepSeek supporta solo `json_object`, non `json_schema` strict, per il messaggio finale). Con
  `function_calling`:

  ```
  HTTP 400 — Thinking mode does not support this tool_choice
  ```

  `deepseek-v4-flash` è **thinking di default** (l'assunzione iniziale *"non-thinking"* è stata
  smentita empiricamente). Risolto: `structured_method="function_calling"` + disattivazione
  thinking via `extra_body={"thinking": {"type": "disabled"}}` (sintassi DeepSeek, **diversa** da
  Qwen).

## Decisione

Structured-output **provider-aware**, con la sintassi provider-specifica riempita dal factory (che
conosce il provider), non accumulata come flag nel client:

- **`invoke_structured` (`structured.py`)** accetta un parametro keyword-only
  `structured_method: str = "json_schema"` (default preserva il comportamento di
  OpenAI/Anthropic/OpenRouter). Riga ~102 usa `method=structured_method`.
- **`OpenAICompatibleClient.__init__` (`openai_compatible_client.py`)** accetta
  `structured_method: str = "json_schema"` e `thinking_extra_body: dict | None = None`. Se
  `thinking_extra_body` è valorizzato, viene iniettato come `extra_body` (kwarg di **primo
  livello** di `ChatOpenAI` in `langchain-openai` 1.3.2, **slot separato** da `model_kwargs`/
  `top_p` — nessuna collisione).
- **`factory.py`**: case `qwen` passa `structured_method="function_calling"` +
  `thinking_extra_body={"enable_thinking": False}`; case `deepseek` passa
  `structured_method="function_calling"` + `thinking_extra_body={"thinking": {"type": "disabled"}}`.
  Case `openai`/`anthropic` **invariati**.
- **Il prompt template frozen NON è toccato**: la sintassi thinking-off e il `method` sono a
  livello di client/transport. **Comparabilità certificata**: `prompt_template_hash` identico
  (`c0bf92c343a691fecb7bcd9afdb3f02a0af5c9494881bfa417780770950a45af`) su tutti e 4 gli agent
  (verificato via query su `runs`).

## Conseguenze

### Positive
- Tutti e 4 gli agent superano l'invoke e girano su **accesso diretto** (M5-T14 completo).
- Qwen e DeepSeek con `function_calling` hanno `fallback_used=false` (structured-output pulito al
  primo colpo, come Anthropic tool-use).

### Negative / LIMITE DI TESI (da dichiarare in RESEARCH §7)
- I due provider cinesi girano **non-thinking forzato**. Da dichiarare: il thinking è disattivato
  per **necessità di protocollo** (structured-output incompatibile col thinking su entrambi), non
  per scelta.
- Finding importante da precisare: **thinking OFF ≠ perdita di reasoning espresso**. I modelli
  producono comunque `action_reasoning`/`portfolio_reasoning` articolati (es. Qwen ha ragionato su
  Bollinger squeeze e funding asimmetrici in un HOLD; DeepSeek su RSI oversold e funding decay in 3
  trade direzionali). Si disattiva la **fase di reasoning interna/nascosta**, non la capacità di
  ragionamento **espresso nell'output**.

### Neutre (trade-off accettati)
- Contrasto architetturale tra provider: i due **USA** (OpenAI `json_schema` nativo, Anthropic
  tool-use thinking-compatibile) vs i due **CN** (entrambi thinking-ON di default, entrambi
  richiedono `function_calling` + thinking forzato OFF, con **sintassi diverse**). Osservazione da
  riportare **con cautela**: è un dettaglio di API design, non una legge; `N=1` tick per modello,
  **contesti diversi** per via del wallet sequenziale (Opzione 2) — il confronto comportamentale
  valido richiede same-tick context in M7.

## Alternative considerate

### Alternativa A: iniettare la parola "json" nel prompt (per Qwen)
- Pro: sblocca `json_schema` sul gateway Alibaba senza cambiare `method`.
- Contro: cambierebbe il `prompt_template_hash`.
- Scartata perché: romperebbe la comparabilità — Qwen/DeepSeek vedrebbero un prompt **diverso**
  dagli altri agent.

### Alternativa B: `json_object` nudo per DeepSeek + affidarsi al fallback
- Pro: `json_object` è supportato da DeepSeek.
- Contro: `json_object` **non forza lo schema** → `fallback_used ≈ 100%`.
- Scartata perché: si misurerebbe l'**euristica di recupero**, non lo structured-output del
  modello. `function_calling` (tool-use con schema) forza lo schema → fallback 0%.

### Alternativa C: tenere `qwen3.7-max` in thinking pieno (adattando tutto il resto)
- Pro: preserverebbe il regime thinking nativo del modello.
- Contro: **tecnicamente impossibile**.
- Scartata perché: il thinking rompe lo structured-output in **entrambe** le strade, verificato con
  due 400 distinti (`json_schema` → vincolo "json"; `function_calling` → tool_choice vietato in
  thinking mode).

## Test gating

I test esistenti (`tests/ -k "structured or llm or factory"`) restano **verdi** col default
`json_schema` invariato (**111 passed**). Aggiungere/annotare test che verifichino:
- il `method` di default resta `json_schema` per `openai`/`anthropic`;
- `qwen`/`deepseek` ricevono `function_calling`;
- `thinking_extra_body` iniettato come `extra_body` **separato**, senza collisione con `top_p`.

## Propagazione

- [x] `src/aiat/llm/structured.py`: parametro `structured_method`
- [x] `src/aiat/llm/openai_compatible_client.py`: `structured_method` + `thinking_extra_body`
- [x] `src/aiat/llm/factory.py`: case `qwen` e `deepseek`
- [ ] Aggiornare `docs/decisions/README.md` con l'indice di ADR-0029
- [ ] RESEARCH §7: thinking-forced-off dei provider CN come limite/variabile comportamentale
- [ ] Nota di collegamento in ADR-0008 (il confine dev/direct si è materializzato qui)
- [ ] Verificare regime structured-output all'ingresso di eventuali nuovi provider (un confine alla
      volta, come 0023)
