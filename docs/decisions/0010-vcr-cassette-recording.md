# ADR-0010: Registrazione cassette VCR M2-T12 — meccanismo, modelli OpenRouter, limiti

**Data**: 2026-06-14
**Status**: accepted
**Milestone**: M2 (con effetti su M6/M7)
**PRD reference**: §9.4 (test strategy / VCR), §8.2 (`invoke_structured`), §8.3 (`StatsCallbackHandler`)
**Closes deferral**: none (estende ADR-0008)

## Contesto

M2-T12 richiede di registrare le cassette VCR per i 15 test di
`tests/integration/test_llm_providers.py`, che esercitano
`OpenAICompatibleClient` via OpenRouter (modalità sviluppo, ADR-0008). Durante
la registrazione sono emersi fatti non previsti dall'handoff:

1. **`vcr_config` non era una fixture.** `tests/conftest.py` definiva
   `vcr_config` come dict a livello di modulo. `pytest-recording` lo risolve via
   `request.getfixturevalue("vcr_config")`: un dict globale viene **ignorato** e
   il plugin usa il proprio default vuoto. Verificato empiricamente: con il dict
   globale, `vcr_config` effettivo `= {}` e `record_mode` resta `"none"` anche con
   `VCR_RECORD_MODE=once`. Conseguenza: la registrazione non si attivava mai e
   l'SDK OpenAI rilanciava la `CannotOverwriteExistingCassetteException` di vcrpy
   come `APIConnectionError` (la diagnosi "async flush" dell'handoff era errata).
   Inoltre `filter_headers` non era applicato → rischio di scrivere la chiave.

2. **Solo alcuni modelli supportano `response_format=json_schema` via
   OpenRouter** (il client usa `method="json_schema"`, ADR-0008 / commit cb9124b):
   - `openai/gpt-4o`, `openai/o3-mini`, `deepseek/deepseek-chat`,
     `deepseek/deepseek-r1`, `qwen/qwen3-235b-a22b-2507` → **OK**.
   - `anthropic/claude-*` → **HTTP 400** ("provider returned error"): Anthropic
     usa tool-use, non `response_format`. Fondamentale, nessun modello claude
     funziona così via OpenRouter.
   - `qwen/qwen3-235b-a22b` (variante *thinking*) → `LengthFinishReasonError`:
     consuma tutti i `max_tokens` ragionando prima di emettere il JSON.
   - Gli slug originali `anthropic/claude-3-5-sonnet` e `qwen/qwen3-235b-a22b`
     (thinking) non producono cassette valide; `deepseek-r1` come test
     *structured* è lento (>90s).

3. **Il costo dell'attempt primario fallito non è contabilizzato.** Con
   `method="json_schema"` l'SDK OpenAI valida la risposta dentro `_agenerate`
   (`beta.chat.completions.parse` → `model_validate_json`): un fallimento di
   validazione lancia lì, quindi langchain emette `on_llm_error` (non
   `on_llm_end`). `StatsCallbackHandler` conta solo `on_llm_end`, e `on_llm_error`
   non porta usage → l'attempt primario fallito (ma fatturato) non entra nel
   ledger. `include_raw=True` **non** risolve (l'errore è nello step LLM).

## Decisione

1. **`vcr_config` diventa una `@pytest.fixture`** in `tests/conftest.py`, con
   `record_mode` da `VCR_RECORD_MODE` (default `none`), `filter_headers`
   `[authorization, x-api-key]`, `match_on` con `body`, `cassette_library_dir`
   `tests/cassettes`, e `decode_compressed_response=True` (cassette leggibili e
   doctorabili).

2. **Slug OpenRouter per le cassette di sviluppo** (solo dev; l'esperimento usa
   provider diretti a M6/M7, ADR-0008):
   - #1/#5/#6/#7/#10/#11/#12 → `openai/gpt-4o`; #13 → `openai/o3-mini`.
   - #3 (structured) → `deepseek/deepseek-chat`; #15 (reasoning) →
     `deepseek/deepseek-r1` (timeout di registrazione a 180s).
   - #4 → `qwen/qwen3-235b-a22b-2507` (variante instruct, non-thinking).
   - #2/#8/#14 → `anthropic/claude-sonnet-4.5` (cassette **sintetiche**, vedi 3).

3. **Cassette miste — reali + sintetiche** (replay sempre via `@pytest.mark.vcr`,
   `record_mode=none`, senza rete):
   - **Reali** (auto-registrate eseguendo il test con `VCR_RECORD_MODE=once`):
     OpenAI/DeepSeek/Qwen structured, cost, reasoning, e auth 401.
   - **Sintetiche** (`scripts/build_synthetic_cassettes.py`, zero chiamate):
     gli scenari non producibili a comando — fallback malformed→valido (#5/#12),
     doppio malformed→unrecoverable (#6), HTTP 429 (#10), e le cassette Anthropic
     (#2/#8/#14, OpenAI-style: claude non supporta `json_schema` via OR). I body
     di richiesta sono **derivati** dalla cassetta gpt-4o reale (il matcher body
     di vcrpy è JSON-aware → confronto order-independent).

4. **`test_timeout_handling` non usa cassetta**: vcrpy fa replay istantaneo, un
   timeout wall-clock non è riproducibile. Si inietta un `ainvoke` lento
   (`patch.object(ChatOpenAI, "ainvoke", ...)`) per esercitare il vero
   `asyncio.wait_for` in `invoke_structured`.

5. **Limite noto accettato sul cost ledger**: l'attempt structured che fallisce
   la validazione non è contabilizzato (vedi Contesto §3).
   `test_cost_aggregation_primary_plus_fallback` asserisce il contratto reale
   (`fallback_used=True`, `n_attempts==1`, costo del fallback > 0). Documentato in
   `StatsCallbackHandler` e nel docstring del test. **Impatto pratico nullo**
   sull'esperimento: i modelli scelti onorano `json_schema` strict → il primario
   non fallisce e il fallback non scatta.

## Conseguenze

### Positive
- Registrazione e replay funzionanti con un unico meccanismo; replay in CI senza
  rete né chiavi. Chiave mai persistita (`filter_headers` ora attivo + verifica
  `grep sk-or-`).
- Cassette leggibili/diff-abili (gzip decodificato), rigenerabili in modo
  deterministico (script sintetico + `VCR_RECORD_MODE=once` per le reali).

### Negative
- Le cassette Anthropic sono sintetiche: non esercitano una risposta claude reale
  via OR (claude non supporta `json_schema` via OR). Copertura nativa Anthropic =
  unit test sintetici (ADR-0008) + verifica live diretta a M6.
- Cost ledger: undercounting dell'attempt primario fallito su fallback (limite
  langchain/SDK, immateriale per i modelli dell'esperimento).

### Neutre (trade-off accettati)
- Slug dei modelli dev cambiati rispetto all'handoff (gli originali non
  funzionavano via OR). Sono slug di sviluppo, non i `model_id` registrati nel
  seed (D1 resta aperto a M7).

## Alternative considerate

### Alternativa A: registrare tutto come reale via pytest
- Contro: fallback malformed / 429 / doppio-malformed non producibili a comando;
  Anthropic 400; qwen-thinking length-limit. Scartata: parte degli scenari è
  intrinsecamente non registrabile da una chiamata reale.

### Alternativa B: ristrutturare `invoke_structured` PATH 1 (bind raw + validazione propria) per contare il primario fallito
- Pro: `n_attempts==2` e costo accurato sul fallback.
- Contro: cambia il percorso structured scelto per la compat OpenRouter, riscrive
  ~6 unit test + 3 mock helper, richiede ri-verifica del routing SDK `parse()`.
- Scartata **per ora**: impatto pratico nullo (i modelli dell'esperimento non
  fanno fallback). Tracciata come lavoro futuro se si userà un provider senza
  `json_schema` in produzione.

### Alternativa C: `on_llm_error` incrementa `n_attempts`
- Contro: i token del primario fallito restano non catturati (`on_llm_error` non
  porta usage) → `n_attempts` e costo incoerenti. Scartata.

## Test gating

- `tests/integration/test_llm_providers.py` (15 test) in `record_mode=none`:
  replay senza rete, tutti verdi. Verifica anti-leak: `grep -r "sk-or-"
  tests/cassettes/` vuoto e nessun header `authorization/x-api-key`.
- `tests/unit/llm` resta verde (81 test, coverage llm ≥95%).

## Propagazione

- [x] Implementato: `tests/conftest.py` (fixture `vcr_config`),
      `tests/integration/test_llm_providers.py`, `scripts/build_synthetic_cassettes.py`,
      14 cassette in `tests/cassettes/`.
- [x] Nota di limite in `src/aiat/llm/stats_handler.py` (docstring).
- [ ] M6: registrare cassette reali dei provider diretti (gateway `direct`),
      inclusa la verifica live Anthropic (tool-use) — chiude il confine ADR-0008.
- [ ] (Eventuale) Alternativa B se un provider senza `json_schema` entra in
      produzione.
- [ ] (PRD non modificato: frozen)
