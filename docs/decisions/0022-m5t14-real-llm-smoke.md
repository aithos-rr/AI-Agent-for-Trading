# ADR-0022: M5-T14 eseguito con LLM reali invece che mockato

**Data**: 2026-06-29
**Status**: accepted (ratificato da Riccardo, 2026-06-29)
**Milestone**: M5-T14 (smoke locale multi-agent), anticipa parte di M6
**PRD reference**: §12 M5 (smoke con LLM mockato); RESEARCH §7 (limitazioni dichiarate)
**Closes deferral**: none

## Contesto

Il PRD prescrive che lo smoke di M5 (M5-T14, 1 orchestrator + 4 agent multi-tick su Postgres)
giri con **LLM mockato**. Riccardo dispone delle 4 API key reali (openai/anthropic/deepseek/
qwen) e di un setup HL testnet, e propone di eseguire M5-T14 con **LLM reali + HL testnet**,
non mockati. Questo è uno scostamento dal PRD, tracciato qui.

## Decisione

Eseguire M5-T14 con **API LLM reali** (gateway=`direct`, un provider per agent) e
**`RealHyperliquidClient` su testnet** (`AIAT_HL_CLIENT_IMPL=real`), invece del mock.

### Procedura — Opzione 2 (un agent per volta, 1 wallet testnet reale)

Lo smoke è eseguito **un agent alla volta**, riusando **un solo wallet testnet reale funded**.
Prima di testare un model, il suo `models.wallet_address` nel DB viene aggiornato all'address
reale; gli altri 3 restano i placeholder del seed → la UNIQUE non è mai violata perché **un
solo model alla volta** ha l'address reale (A3 match soddisfatto per quel model). Procedura
dettagliata in `docs/runbooks/m5t14-smoke.md`.

**Ordine di test** (dal formato structured-output più maturo al più a rischio sorpresa, così
il confound emerge dove più probabile): **anthropic (`usa-premium`) → openai (`usa-cheap`) →
qwen (`cn-premium`) → deepseek (`cn-cheap`)** (qwen/deepseek via OpenAI-compatible).

## Motivazione

- **Il mock LLM non aggiunge valore allo smoke**: una chiamata LLM costa frazioni di centesimo;
  per uno smoke di pochi tick il costo è trascurabile.
- **Il mock nasconderebbe il confound che lo smoke deve scoprire**: il formato di structured
  output **differisce tra provider** (openai/anthropic usano meccanismi diversi — response_format
  json_schema vs tool-use; deepseek/qwen via OpenAI-compatible). È esattamente il rischio che lo
  smoke multi-agent deve far emergere (parsing reale, fallback freetext, eventuali rifiuti safety
  di DeepSeek/Qwen — cfr. risk register S5). Con il mock questo resta invisibile fino a M6/M7.
- **Coerenza con M4-T08/M3-T11**: già validati contro fonti/exchange reali su testnet; usare reale
  anche qui mantiene un'unica linea di validazione e anticipa lavoro di M6.

## Conseguenze

### Positive
- Lo smoke esercita il **vero** percorso end-to-end (LLM reale → structured output → guardrail →
  RealHyperliquidClient testnet → outcomes), scoprendo confound provider-specifici prima di M6.
- Anticipa parte della validazione M6 (servizi reali).

### Negative / Note (limiti temporanei, da dichiarare)
- **Deviazione dal PRD §M5** (mock → reale): implementativa/operativa, non architetturale.
- **Isolamento portfolio**: se per lo smoke si usasse un wallet condiviso si violerebbe l'inv #1
  a livello di portfolio — accettabile per lo smoke, **non** per M7. Ma c'è un **blocco aperto**
  (vedi sotto): `models.wallet_address` è UNIQUE e il seed registra 4 wallet distinti, mentre A3
  pretende il match per-model → non si può usare un solo wallet condiviso. Da risolvere con
  Riccardo prima dello smoke.
- **Costo API**: trascurabile per lo smoke (pochi tick × 4 modelli).
- **Determinismo**: temp=0 + seed=42 riducono ma non azzerano la varianza (i provider non
  garantiscono determinismo assoluto) — atteso, non un bug.
- **Cost-tracking** (osservazione collaterale, non bloccante): `load_llm` cerca il pricing per
  `model_name_api`, mentre `model_pricing.yaml` è ora keyed per `model_id` → in mancanza di una
  chiave per `model_name_api` il client usa il pricing di fallback. Da allineare prima che i
  numeri di costo contino (M6/M7), irrilevante per la correttezza dello smoke.

## Nodo wallet — RISOLTO con Opzione 2

A3 (match `hl_wallet_address` ↔ `models.wallet_address`) + UNIQUE su `models.wallet_address`
impedivano di usare **un solo** wallet reale per i 4 agent *simultaneamente*. **Scelta: Opzione
2** — un agent per volta, riusando un solo wallet reale, aggiornando `models.wallet_address` del
model sotto test all'address reale (un solo model alla volta lo possiede → UNIQUE intatta). Le
altre opzioni (4 wallet distinti funded; HL sub-account) restano per M6, dove serve la
concorrenza reale.

### Limite dichiarato (threats to validity)

Questo smoke valida **ogni agent end-to-end col suo provider reale per ~4 tick**, **NON** la
**concorrenza di 4 agent simultanei**. Quest'ultima (parità byte-identica del market context
cross-model, inv #13; isolamento portfolio cross-model, inv #1) è **già coperta dai test e2e
automatici** `tests/e2e/test_context_parity.py` e `tests/e2e/test_isolation.py` (verdi nel
gate). La concorrenza reale a 4 wallet distinti si osserverà a **M6**.

## Propagazione

- [x] Ratificato da Riccardo (2026-06-29) → status `accepted`
- [x] Nodo wallet risolto: **Opzione 2** (un agent per volta, 1 wallet reale, swap address nel DB)
- [x] `.env.agent.template` (template di configurazione per un agent reale, .gitignored)
- [x] Runbook operativo: `docs/runbooks/m5t14-smoke.md`
- [x] Indicizzato in `docs/decisions/README.md`
- [ ] Esecuzione osservata da Riccardo in WSL (human-gated) → chiusura M5-T14
