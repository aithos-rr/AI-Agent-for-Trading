# ADR-0009: Classificazione eccezioni LLM — isinstance() primario + string-match fallback

**Data**: 2026-06-13
**Status**: accepted
**Milestone**: M2
**PRD reference**: §8.2 (fix B.18 review-r2-v2)
**Closes deferral**: D3

## Contesto

PRD §15.4 deferral D3 richiedeva di decidere la strategia di classificazione
delle eccezioni provider-specific in `_is_rate_limit_error()` e `_is_auth_error()`
di `src/aiat/llm/structured.py`. Il PRD §8.2 forniva una bozza con solo string
matching e indicava esplicitamente di rafforzarla con `isinstance()` al momento
dell'implementazione (fix B.18 review-r2-v2).

Durante l'implementazione M2 abbiamo verificato le SDK OpenAI e Anthropic
installate (versioni in `uv.lock`):
- `openai`: espone `openai.RateLimitError`, `openai.AuthenticationError`, `openai.PermissionDeniedError`
- `anthropic`: espone `anthropic.RateLimitError`, `anthropic.AuthenticationError`, `anthropic.PermissionDeniedError`

DeepSeek e Qwen utilizzano `langchain-openai` con `base_url` custom: le eccezioni
propagate sono di tipo `openai.*` (stesso SDK), quindi già coperte dal path OpenAI.

## Decisione

In `src/aiat/llm/structured.py`, `_is_rate_limit_error()` e `_is_auth_error()`
usano una strategia **a due livelli**:

1. **PRIMARY**: `isinstance()` sulle classi SDK ufficiali, nell'ordine:
   - `openai.RateLimitError` / `openai.AuthenticationError` / `openai.PermissionDeniedError`
   - `anthropic.RateLimitError` / `anthropic.AuthenticationError` / `anthropic.PermissionDeniedError`
   Ogni import è wrappato in `try/except ImportError` per resilienza.

2. **FALLBACK**: string matching su `str(exc).lower()` per provider OpenAI-compatible
   (DeepSeek/Qwen via terze parti) che potrebbero non rispettare sempre le exception
   class del SDK ufficiale, o per eccezioni HTTP raw non wrappate.

La gerarchia è: isinstance() ha precedenza; il fallback copre il residuo.

## Conseguenze

### Positive
- Classificazione deterministica per OpenAI e Anthropic: no falsi positivi da
  string matching su messaggi generici.
- Compatibilità con provider OpenAI-compatible (DeepSeek/Qwen): il fallback
  stringa copre i casi in cui l'SDK OpenAI non wrappa l'eccezione del provider.
- Resilienza all'assenza parziale di SDK (ImportError gestito).

### Negative
- Il fallback stringa rimane fragile per provider che usano messaggi non standard.

### Neutre (trade-off accettati)
- DeepSeek-R1 e Qwen via OpenAI-compatible propagano già `openai.*` errors,
  quindi in pratica il fallback stringa è difensivo, non il percorso primario.

## Alternative considerate

### Alternativa A: Solo string matching (bozza PRD originale)
- Pro: Semplice.
- Contro: Fragile per messaggi non standard; falsi positivi su messaggi che
  contengono "401" in contesti non-auth.
- Scartata perché: meno robusto di isinstance() quando le classi SDK sono disponibili.

### Alternativa B: Solo isinstance() senza fallback
- Pro: Massima precisione.
- Contro: Non copre provider custom che non usano classi SDK standard.
- Scartata perché: alcuni provider OpenAI-compatible avvolgono le eccezioni
  in modo non standard.

## Test gating

`tests/unit/llm/test_exception_classification.py` — verifica isinstance() per
OpenAI e Anthropic (4 test) + string-match fallback (4 test). Eseguito in CI
nel gate core coverage 95% (domain/llm/execution).

## Propagazione

- [x] Implementato in `src/aiat/llm/structured.py` (`_is_rate_limit_error`, `_is_auth_error`)
- [x] Test in `tests/unit/llm/test_exception_classification.py`
- [x] Aggiornare `docs/decisions/README.md` con questo ADR
- [ ] Nessuna migration DDL richiesta
