# ADR-0026: Probe credenziali A7 lightweight (`ping()`) — riallineamento a PRD §10.1

**Data**: 2026-07-01
**Status**: accepted
**Milestone**: M5-T14 (follow-up), vincolante per M6 (smoke 48h) e M7 (avvio agent)
**PRD reference**: §10.1 (startup check A7); §7.3 (`BaseLLMClient`); §8.2 (`invoke_structured`);
ADR-0008 (dual-mode / direct-provider a M6), ADR-0023 (provider-aware sampling)
**Closes deferral**: none (chiude il FOLLOW-UP annotato in `lifecycle.py` post-M5-T14)

## Contesto

Lo startup check **A7** (`_check_llm_credentials` in `src/aiat/orchestration/lifecycle.py`)
valida che le credenziali LLM dell'agent siano valide e il provider raggiungibile, prima di
avviare il decision loop.

L'implementazione era **driftata dal PRD**. Il PRD §10.1 (righe 2703-2711) prescrive A7 come una
**smoke call raw** sul modello langchain sottostante:

```python
# [A7] LLM provider credentials valid (smoke call ~$0.001)
llm = load_llm(settings)
try:
    await asyncio.wait_for(llm._llm.ainvoke("Reply with exactly: pong"), timeout=15)
except (LLMTimeoutError, LLMAuthError, Exception) as e:
    raise RuntimeError(f"LLM provider credentials invalid or unreachable: {e!r}")
```

Il codice reale invece chiamava `llm.invoke("Reply with exactly: pong")`, che passa per
`invoke_structured` (§8.2): `with_structured_output(TradeDecision)` + eventuale fallback freetext
+ validazione Pydantic. Ovvero A7 eseguiva una **decisione strutturata completa** solo per
validare le credenziali. Era quella la deviazione dal PRD, non viceversa.

Due conseguenze concrete emerse a M5-T14 (ADR-0022/0023):

1. **Costo e latenza sproporzionati per un probe.** Un probe credenziali eseguiva l'intero path
   di decisione, con i modelli thinking (Opus 4.8, effort=high) che sforano i 15s — costringendo
   già A7 a usare `hard_timeout_seconds` invece del 15s fisso del PRD (ADR-0023).
2. **Accoppiamento improprio.** Legare la validità delle credenziali alla capacità del modello di
   produrre un `TradeDecision` valido mescola due preoccupazioni: raggiungibilità/autenticazione
   vs. conformità dell'output strutturato. Un fallimento di parsing avrebbe fatto fallire A7 come
   se fosse un problema di credenziali.

Il FOLLOW-UP era già annotato nel codice: *"A7 runs a full structured-output call just to
validate credentials — should be a lightweight ping (tracked post-M5-T14)"*.

## Decisione

Si introduce un metodo **`ping()`** sull'interfaccia `BaseLLMClient` (§7.3):

```python
async def ping(self, *, timeout_seconds: int = 30) -> None:
    """[A7] Lightweight credential probe. Raw ainvoke, NO structured output.
    Raises on empty/failed response. Does NOT validate TradeDecision."""
    raise NotImplementedError
```

- **Metodo concreto, non astratto.** `ping()` NON è `@abstractmethod`: aggiungerlo non impone
  nuovi requisiti alle sottoclassi né rompe la loro istanziabilità. La base solleva
  `NotImplementedError`; i 3 client concreti (`OpenAIClient`, `OpenAICompatibleClient`,
  `AnthropicClient`) fanno override identico:

  ```python
  async def ping(self, *, timeout_seconds: int = 30) -> None:
      import asyncio
      resp = await asyncio.wait_for(self._llm.ainvoke("ping"), timeout=timeout_seconds)
      content = getattr(resp, "content", None)
      if not content:
          raise RuntimeError(f"{self.provider} ping returned empty response")
  ```

- **Raw `ainvoke`, riallineato al PRD §10.1.** `ping()` incapsula esattamente il probe raw che il
  PRD prescrive (`self._llm.ainvoke(...)`), dietro un metodo uniforme sui client invece di
  reachare `._llm` dall'esterno. Bypassa completamente `invoke_structured`/`with_structured_output`
  e non valida alcun `TradeDecision`.

- **`_check_llm_credentials` sostituisce solo la riga di invocazione**, mantenendo il `try/except`
  esterno che riavvolge ogni errore in `RuntimeError("LLM credentials invalid or unreachable: ...")`:

  ```python
  await llm.ping(timeout_seconds=int(settings.hard_timeout_seconds))
  ```

  Il timeout resta `hard_timeout_seconds` (ADR-0023), ora passato come kwarg intero al posto del
  `float(...)` di `asyncio.wait_for` (il `wait_for` si sposta dentro `ping()`).

### Estensione additiva di §7.3 (interfaccia frozen)

`BaseLLMClient` in §7.3 dichiara solo `invoke` come astratto. `ping()` **estende** l'interfaccia
in modo puramente additivo: `invoke` — e con esso l'intero path di decisione reale
(`decision_loop` → `invoke` → `invoke_structured` → `TradeDecision`) — resta **immutato**. La
modifica tocca esclusivamente il percorso del probe di startup.

## Conseguenze

### Positive
- **Riallineamento al PRD §10.1**: A7 torna a essere un probe raw come da blueprint.
- **Probe economico e robusto**: nessuna decisione strutturata per validare credenziali; separa
  raggiungibilità/auth dalla conformità dell'output.
- **Sblocco per costruzione dei provider mancanti**: OpenAI, DeepSeek, Qwen usano lo stesso
  `self._llm.ainvoke` — il probe A7 funziona identicamente su tutti quando entreranno nello smoke
  (ADR-0023, un confine reale alla volta), senza codice provider-specifico aggiuntivo.
- **Non-regressione Opus**: il commit `a426c43` (Opus end-to-end) non è impattato — `invoke` non
  è toccato.

### Neutre / Limiti
- **`invoke_structured`/`json_schema` NON toccati**: il confound direct-provider sull'output
  strutturato reale (`method="json_schema"` tarato su OpenRouter, da verificare sui provider
  diretti) resta **scope M6** (ADR-0008). Questo ADR non lo anticipa né lo modifica.
- **Nessuna validazione semantica nel probe**: `ping()` verifica solo risposta non vuota. È
  intenzionale — la conformità `TradeDecision` è verificata dal path reale, non dal probe.
- **Prompt del probe** `"ping"` (vs. `"Reply with exactly: pong"` del PRD): irrilevante ai fini
  del check, serve solo una risposta non vuota; il contenuto non viene interpretato.

## Test gating

- `tests/unit/orchestration/test_lifecycle.py::test_a7_uses_configured_hard_timeout`: verifica che
  `_check_llm_credentials` invochi `llm.ping(timeout_seconds=123)` con il valore di
  `settings.hard_timeout_seconds` (int, non float), NON un 15 hardcoded.
- Verifica *live* dei percorsi diretti (inclusa la nuova `ping()` su OpenAI/DeepSeek/Qwen) a
  **M6**, con la registrazione delle cassette reali dei provider (ADR-0008).

## Propagazione

- [x] `src/aiat/llm/base.py`: `ping()` concreto (default `NotImplementedError`)
- [x] `src/aiat/llm/openai_client.py`: override `ping()`
- [x] `src/aiat/llm/openai_compatible_client.py`: override `ping()`
- [x] `src/aiat/llm/anthropic_client.py`: override `ping()`
- [x] `src/aiat/orchestration/lifecycle.py`: `_check_llm_credentials` usa `llm.ping()`; commento
      FOLLOW-UP aggiornato (probe lightweight implementato)
- [x] `tests/unit/orchestration/test_lifecycle.py`: test A7 riscritto su `ping`
- [x] Indicizzato in `docs/decisions/README.md`
- [ ] `src/aiat/llm/structured.py`: **NON toccato** (vincolo esplicito)
- [ ] `invoke` in qualsiasi client: **NON toccato** (path decisione reale invariato)
