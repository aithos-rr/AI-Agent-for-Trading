# ADR-0023: Client provider-aware sui sampling params; asimmetria di determinismo cross-model

**Data**: 2026-06-29
**Status**: accepted (ratificato da Riccardo, 2026-06-29)
**Milestone**: M5-T14 (scoperta empirica), vincolante per M7 (analisi varianza)
**PRD reference**: §8.1 (LLM client); ADR-0020 (determinismo), ADR-0022 (smoke M5-T14); RESEARCH §7
**Closes deferral**: none

## Contesto

ADR-0020 prescriveva `temperature=0` + `seed=42` per **tutti** i modelli, per un determinismo
uniforme cross-model. M5-T14 (smoke con LLM reali, ADR-0022) ha rivelato **empiricamente** che
**Anthropic Opus 4.8 (`claude-opus-4-8`) è thinking-only e RIFIUTA `temperature`** con un errore
reale catturato al primo invoke su API Anthropic (startup check A7):

```
HTTP 400 — "`temperature` is deprecated for this model."
```

Causa tecnica (verificata): `langchain-anthropic` inietta `temperature` nel payload solo se non
è `None`; il nostro `AnthropicClient` la passava **sempre** (`temperature=float(temperature)`)
→ 400. Lo stesso vale per `seed`/`top_p` (non supportati dai modelli Anthropic thinking). Quindi
**il determinismo uniforme cross-model NON è raggiungibile**: alcuni provider non espongono i
parametri di sampling.

## Decisione

**Strada A — ogni modello nel suo regime nativo.** I client LLM diventano **provider-aware** sui
sampling params:

- **Anthropic**: NON riceve `temperature` (né `seed`/`top_p`). `AnthropicClient.__init__` rende
  `temperature` opzionale (`Decimal | None = None`) e la passa a `ChatAnthropic` **solo se non
  None**; `factory.py` (case `anthropic`) **non** la passa → omessa. Il client gira nel regime
  thinking nativo del modello.
- **Altri provider** (openai/deepseek/qwen): il regime di sampling va **verificato
  empiricamente** quando ciascuno entra nello smoke M5-T14 — **un confine reale alla volta**
  (non si presume; si osserva, come per Anthropic).
- **Audit fedele**: `LLMInvocationResult.temperature` registra il valore **realmente usato** —
  per Anthropic è `None`, non un falso `0`. La verità dell'audit prevale sul determinismo
  nominale.

## Conseguenze

### Positive
- L'agent Anthropic supera A7 e gira contro l'API reale (sblocca M5-T14 per `usa-premium`).
- L'audit (`temperature`) riflette il regime reale di ogni modello.

### Negative / LIMITE DI TESI (da dichiarare in RESEARCH §7)
- **Asimmetria di determinismo tra i soggetti**: Anthropic è **non-deterministico per
  costruzione** (nessun controllo su temperature/seed); gli altri provider sono
  *potenzialmente* più controllabili. Il determinismo uniforme cross-model di ADR-0020 **non è
  raggiungibile**.
- **Impatto sull'analisi M7**: la **varianza intra-modello** va trattata in modo
  **provider-aware**; la **riproducibilità esatta di un singolo run Anthropic non è garantita**.
- **Validità**: la comparazione **resta valida** — i modelli sono confrontati nel **loro stato
  reale** (condizione ecologica: è così che opererebbero in produzione), non in un regime
  artificiale che alcuni non supportano. Da inquadrare come scelta metodologica, non come difetto.

## Correzione evolutiva di ADR-0020

ADR-0020 §"Parametri deterministici" è da leggersi così: `temperature=0` + `seed=42` si
applicano **solo ai provider che li supportano**; i provider thinking-only (es. Anthropic Opus
4.8) girano nel regime nativo senza sampling params. Correzione evolutiva, **non** riscrittura
di ADR-0020 (la struttura D1 resta invariata); annotata anche in ADR-0020.

## Latenza dei thinking model (seconda faccia della stessa scoperta)

Lo stesso M5-T14 ha rivelato il rovescio del regime thinking: **Opus 4.8 con effort=high ha
latenze elevate** — una singola decisione strutturata **supera i 15s** (e può superare i 90s).
I timeout fissi bassi sparsi nel codice tagliavano la chiamata prima del completamento:

- **A7** (`lifecycle._check_llm_credentials`): aveva `timeout=15.0` hardcoded → ora usa
  `float(settings.hard_timeout_seconds)` (180 per l'agent). *Follow-up annotato*: A7 esegue una
  vera chiamata structured-output solo per validare le credenziali — andrebbe ridotta a un ping
  leggero (post-M5-T14).
- **decision_loop step [5]** (`decision_loop.py`): l'invoke LLM aveva `timeout_seconds=90`
  hardcoded → ora usa `self._settings.hard_timeout_seconds`, **allineato** all'outer
  `run_once` `asyncio.wait_for(..., timeout=hard_timeout_seconds)` (che già bounda il tick).
  L'inner non taglia più prima dell'outer; il budget reale del tick resta `hard_timeout_seconds`.

Decisione (Opzione 1, ratificata): **usare il setting `hard_timeout_seconds` esistente** (PRD:
agent 180 / orchestrator 30), non valori fissi. Nessun parametro nuovo.

**Conseguenza per la tesi**: la **latenza-per-decisione è un dato osservabile** che varia per
provider (thinking vs non-thinking) — da riportare in M7 accanto all'asimmetria di determinismo
(stessa famiglia di effetti del regime thinking). I thinking model costano più tempo (e token di
reasoning) per decisione; va dimensionato il budget e misurato come variabile comportamentale.

## Test gating

`tests/unit/llm/test_anthropic_client.py`: `temperature=None` (default) → **non** passata a
`ChatAnthropic`; `LLMInvocationResult.temperature` è `None`; con `temperature=Decimal("0")`
esplicita → **passata** (retrocompatibilità per provider che la accettano). Mock di
`ChatAnthropic`, nessuna chiamata reale.

## Propagazione

- [x] `src/aiat/llm/anthropic_client.py`: `temperature: Decimal | None = None`, omessa se None
- [x] `src/aiat/llm/factory.py` (case anthropic): non passa temperature
- [x] `src/aiat/orchestration/lifecycle.py` (A7): timeout = `hard_timeout_seconds` (non 15s fisso)
- [x] `src/aiat/orchestration/decision_loop.py` (step 5): timeout = `hard_timeout_seconds` (non 90s)
- [x] Test unit (omissione None / pass esplicito / result None; A7 usa il setting)
- [x] Nota evolutiva in ADR-0020
- [x] Indicizzato in `docs/decisions/README.md`
- [ ] Regime sampling di openai/deepseek/qwen: verificato man mano in M5-T14
- [ ] Asimmetria di determinismo → da scrivere in RESEARCH §7 (limite) per M7
