# ADR-0008: Routing LLM dual-mode — OpenRouter (sviluppo) / provider diretti (esperimento)

**Data**: 2026-06-13
**Status**: accepted
**Milestone**: M2 (con effetti fino a M6/M7)
**PRD reference**: §8.1 (factory `load_llm`), §8.3 (`StatsCallbackHandler`), §9 (test strategy)
**Closes deferral**: none (deviazione strutturale dal PRD §8 — non chiude una D1-D5)

## Contesto

Il `PRD_V2.md` §8.1 definisce una factory `load_llm(settings)` che instrada
verso 4 client, ciascuno dei quali accede al provider LLM **direttamente**:

| Provider | Client (§8) | Dominio / `base_url` | Chiave |
|----------|-------------|----------------------|--------|
| OpenAI | `OpenAIClient` | `api.openai.com` | `openai_api_key` |
| Anthropic | `AnthropicClient` | `api.anthropic.com` | `anthropic_api_key` |
| DeepSeek | `OpenAICompatibleClient` | `https://api.deepseek.com/v1` | `deepseek_api_key` |
| Qwen | `OpenAICompatibleClient` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `qwen_api_key` |

Tutti derivano da `BaseLLMClient`. Sono quindi **4 domini** e **4 chiavi API**
distinte.

In fase di **sviluppo** (M2-M5) questo crea attrito concreto:

- Il devcontainer ha un firewall in whitelist che dovrebbe aprire 4 domini
  separati per esercitare i client.
- Servirebbero 4 chiavi reali, una per provider, solo per registrare le
  cassette VCR (§9) e verificare i client durante il loop di sviluppo.

`OpenRouter` (`https://openrouter.ai/api/v1`) è un gateway **OpenAI-compatibile**
che dà accesso a tutti i modelli con **una** chiave e **un** dominio, riusando
esattamente l'`OpenAICompatibleClient` che il PRD §8 già prevede: cambia solo il
`base_url`. La selezione del modello segue la convenzione OpenRouter
(`"openai/gpt-4o"`, `"anthropic/claude-..."`, ecc.).

Poiché questo introduce un percorso di routing **non** descritto nel PRD §8.1
(che conosce solo il dispatch diretto), per la regola del `CLAUDE.md` —
*deviare dal PRD richiede un ADR* — questa decisione viene tracciata qui.

### Razionale scientifico (protezione della validità dell'esperimento)

OpenRouter è un **intermediario**: può instradare la stessa richiesta verso
host, deployment o versioni diverse di un modello — soprattutto per i modelli
open come DeepSeek e Qwen, dove più provider espongono lo stesso peso con
configurazioni di serving differenti. Per un esperimento comparativo rigoroso
tra 4 LLM (RESEARCH §3 — confronto cross-model), l'intermediario sarebbe un
**confound**: una differenza misurata fra modelli potrebbe in realtà essere
una differenza fra host di OpenRouter.

Quindi: OpenRouter è accettabile in **sviluppo** (dove conta solo che il codice
funzioni end-to-end), ma l'**esperimento ufficiale** (M6/M7) usa i provider
diretti, eliminando l'intermediario come variabile. Il dual-mode è perciò una
scelta che **protegge** la validità dell'esperimento, non un compromesso che la
indebolisce.

## Decisione

Si introduce una variabile d'ambiente `AIAT_LLM_GATEWAY` con valori
`{direct, openrouter}` e **default `direct`**. La factory `load_llm()` (§8.1)
esegue lo switch:

- `gateway=openrouter` → **tutti** i modelli passano per
  `OpenAICompatibleClient` con `base_url="https://openrouter.ai/api/v1"` e
  chiave `AIAT_OPENROUTER_API_KEY`; il modello si seleziona con la convenzione
  OpenRouter (es. `"openai/gpt-4o"`, `"anthropic/claude-..."`).
- `gateway=direct` → dispatch nativo del PRD §8.1 **invariato**: `OpenAIClient`,
  `AnthropicClient`, e `OpenAICompatibleClient` per DeepSeek/Qwen con i loro
  `base_url` e chiavi dirette.

Il default `direct` è una scelta **fail-safe** per la validità scientifica: se
l'esperimento (M6/M7) viene avviato senza impostare il gateway, usa i provider
diretti — il percorso rigoroso — non l'intermediario.

### Principio additivo (cruciale)

Questa decisione è **additiva, non sostitutiva**:

- **Tutti** i client del PRD §8 (`OpenAIClient`, `AnthropicClient`,
  `OpenAICompatibleClient`) vengono implementati e restano attivi. OpenRouter
  **non** rimpiazza alcun client: è un `base_url` alternativo selezionato a
  runtime dalla factory. Nessun codice del PRD viene rimosso, commentato o
  disabilitato.
- Il passaggio sviluppo→esperimento è **solo** un cambio di variabile
  d'ambiente (`AIAT_LLM_GATEWAY=direct` + le 4 chiavi dirette), **non** una
  modifica al codice. Il codice è **identico** nelle due modalità; cambia solo
  la configurazione runtime.

### Confine di scope sui test

Conseguenza diretta del dual-mode, esplicitata e accettata:

- In modalità `openrouter`, le cassette VCR di sviluppo esercitano il percorso
  `OpenAICompatibleClient` (formato risposta OpenAI-style). I percorsi nativi
  `OpenAIClient` e `AnthropicClient` (usati nell'esperimento) **non** sono
  esercitati dalle cassette di sviluppo.
- In particolare `StatsCallbackHandler` (§8.3) deve estrarre i token da formati
  **diversi** per provider:
  - OpenAI: `prompt_tokens` / `completion_tokens`
    (`response_metadata['token_usage']`);
  - Anthropic: `input_tokens` / `output_tokens` (`response.usage`).
  Per coprire i formati nativi **senza** chiamate reali in sviluppo, i test
  **unit** di `StatsCallbackHandler` usano risposte **sintetiche** nei formati
  di ogni SDK (già previsto da §9).
- Le cassette **reali** dei provider diretti si registrano a **M6** (setup
  esperimento) con le chiavi dirette.

Questo confine è esplicito e accettato: i percorsi nativi sono wrapper sottili
coperti da unit test con dati sintetici in M2; la verifica **live** diretta
avviene a M6.

## Conseguenze

### Positive
- Sviluppo semplificato: **una** chiave (`AIAT_OPENROUTER_API_KEY`), **un**
  dominio firewall (`openrouter.ai`).
- Cassette VCR registrabili dal loop di sviluppo senza 4 chiavi separate né 4
  domini in whitelist.
- Architettura del PRD §8 **invariata**: tutti i client restano, nessuna
  rimozione (principio additivo).
- Validità dell'esperimento protetta dal default `direct` (fail-safe).

### Negative
- I percorsi nativi diretti (`OpenAIClient`, `AnthropicClient`) **non** sono
  testati *live* fino a M6 — mitigato da unit test con dati sintetici nei
  formati SDK (§8.3 / §9).
- Dipendenza da OpenRouter come servizio terzo durante lo sviluppo.
- Necessità di documentare e verificare a M6 lo switch a `direct` (con le 4
  chiavi dirette e la registrazione delle cassette reali) prima dell'esperimento
  ufficiale.

### Neutre (trade-off accettati)
- Il firewall del devcontainer va esteso con `openrouter.ai` — operazione di
  setup separata, non parte di questo ADR.
- In modalità `openrouter` la selezione del modello cambia convenzione di
  naming (`provider/model`); è un dettaglio di configurazione, non di codice.

## Alternative considerate

### Alternativa A: provider diretti da subito (PRD §8 as-is)
- Pro: nessun confound da intermediario; percorso identico in sviluppo ed
  esperimento.
- Contro: 4 domini da aprire nel firewall del devcontainer e 4 chiavi reali
  necessarie già in sviluppo, con attrito significativo per il loop e per la
  registrazione delle cassette.
- Esito: **scartata per lo sviluppo**, **adottata per l'esperimento** (è
  esattamente la modalità `direct`, default a M6/M7).

### Alternativa B: OpenRouter per tutto (anche per l'esperimento)
- Pro: massima semplicità operativa, una sola chiave/dominio ovunque.
- Contro: l'intermediario instrada potenzialmente verso host/versioni diverse
  dei modelli (in particolare DeepSeek/Qwen), introducendo un **confound**
  nell'esperimento comparativo ufficiale.
- Scartata per l'esperimento: indebolirebbe la validità scientifica del
  confronto cross-model (RESEARCH §3). OpenRouter resta confinato allo sviluppo.

## Test gating

- `tests/unit/llm/test_factory.py` (M2): verifica lo switch della factory —
  `AIAT_LLM_GATEWAY=openrouter` → `OpenAICompatibleClient` con
  `base_url="https://openrouter.ai/api/v1"`; `AIAT_LLM_GATEWAY=direct` (e
  default in assenza della variabile) → dispatch nativo §8.1 invariato per i 4
  provider.
- `tests/unit/llm/test_stats_handler.py` (M2): verifica l'estrazione token sui
  formati nativi con risposte **sintetiche** — OpenAI
  (`prompt_tokens`/`completion_tokens`) e Anthropic
  (`input_tokens`/`output_tokens`) — così i percorsi diretti sono coperti senza
  chiamate reali.
- A **M6**: registrazione delle cassette reali dei provider diretti (gateway
  `direct`, 4 chiavi) → verifica *live* dei percorsi nativi prima
  dell'esperimento.

## Propagazione

Impatti futuri di questa decisione (file da aggiornare in seguito — **non**
modificati da questo ADR):

- [x] Documentata la decisione in questo ADR
- [ ] `factory load_llm()` (M2-T09): implementa lo switch su `AIAT_LLM_GATEWAY`
- [ ] `.env.example`: aggiungere `AIAT_LLM_GATEWAY` e `AIAT_OPENROUTER_API_KEY`
- [ ] `settings` (`AgentSettings`, M5): validare `gateway` + chiave coerente
      con la modalità selezionata
- [ ] `init-firewall.sh`: aggiungere `openrouter.ai` alla whitelist del
      devcontainer
- [ ] M6 (setup esperimento): switch a `gateway=direct` + 4 chiavi dirette +
      registrazione cassette reali dei provider
- [ ] (PRD non modificato: frozen)
