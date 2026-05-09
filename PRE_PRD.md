# PRE-PRD — AI Trading Agent V2 (Thesis Edition)

> Documento di design preliminare. Cristallizza le decisioni strategiche prese tra il 9 maggio 2026 e oggi, prima del PRD tecnico definitivo.
> Input alla Fase 2 (analisi repo avanzata) e alla Fase 4 (PRD V2).
> Branch: `prd/v2-design` — non mergiato in `main` finché il PRD non è completo.

---

## 0. Contesto e scopo

### 0.1 Natura del progetto

V2 è la **versione scientifica** dell'AI Trading Agent, progettata per essere oggetto di una tesi di laurea triennale in *Filosofia e Intelligenza Artificiale* (Sapienza). Non è un prodotto commerciale, non è un bot per generare profitto. È uno **strumento di indagine empirica** sul comportamento di Large Language Models come decisori autonomi in un dominio ad alta variabilità (crypto-perpetuals).

### 0.2 Domande di ricerca

La tesi indaga tre dimensioni interconnesse:

1. **Fattibilità** — È economicamente e operativamente sostenibile usare un LLM come decisore di trading? In che condizioni?
2. **Spiegabilità** — Le decisioni di un LLM sono ricostruibili, coerenti, confrontabili a posteriori?
3. **Proprietà emergenti dato il contesto** — A parità di prompt e contesto, emergono nei modelli pattern comportamentali sistematici non riducibili al prompt stesso?

### 0.3 Risultato atteso della tesi

Un dataset originale (4 settimane × 4 modelli × cron 15m ≈ 11.000 osservazioni decisionali totali) con metriche complete su fattibilità, spiegabilità e comportamento, sufficiente a sostenere affermazioni difendibili sulle tre dimensioni di ricerca.

### 0.4 Ambito esplicitamente fuori scope

- Generazione di profitto come obiettivo
- Trading su mainnet con capitale reale
- Confronto con strategie quantitative classiche (può entrare come riferimento, non come oggetto di studio)
- Pubblicazione del codice come prodotto open-source

---

## 1. Decisioni strategiche (consolidate)

### 1.1 Selezione dei modelli LLM

**Decisione**: 4 modelli LLM in design 2×2 (geografia × tier).

| Cella | Geografia | Tier | Candidato (verifica pricing in Fase 4) |
|-------|-----------|------|------------------------------------------|
| A | USA | Premium reasoning | OpenAI GPT-5.1 |
| B | USA | Premium concorrente | Anthropic Claude Sonnet 4.6 (o Opus 4.6) |
| C | Cina | Premium low-cost | DeepSeek-R1 o DeepSeek-V3 |
| D | Cina | Alternativa | Qwen 2.5-Max o Kimi K2 |

**Razionale**: il design 2×2 (USA vs CN; premium vs cheap-or-alternative) genera affermazioni più ricche del semplice "4 modelli", perché permette analisi marginali (effetto geografia tenendo costante il tier, ed effetto tier tenendo costante la geografia).

**Aperto in PRD**: scelta finale dei 4 specifici, in funzione di pricing API attuale, disponibilità (no waitlist), supporto a structured output JSON Schema, supporto a reasoning trace recuperabile.

### 1.2 Architettura multi-modello

**Decisione**: 4 servizi Railway separati, 1 DB Postgres condiviso.

Ogni servizio è un deployment indipendente (`agent-openai`, `agent-anthropic`, `agent-deepseek`, `agent-qwen`) con il proprio set di env vars (`MODEL_ID`, `LLM_PROVIDER`, `WALLET_ADDRESS`, `PRIVATE_KEY`, `LLM_API_KEY`). Tutti puntano allo stesso DB e scrivono distinguendosi per `model_id`.

**Razionale**:
1. **Isolamento dei guasti**: un'API LLM down non blocca gli altri 3 modelli.
2. **Onestà scientifica**: ogni modello ha latenza, retry, logging completamente isolati. Niente interferenze tra loro.
3. **Replicabilità**: il design "stesso codice, env diverse" è il pattern *idiomatic* per esperimenti comparativi e si racconta in 2 righe in tesi.

**Trade-off accettato**: 4× costo deployment Railway (Hobby plan). Mitigato dal fatto che Hobby costa pochi $/mese per servizio piccolo, e la durata sperimentale è 4 settimane.

**Da motivare in tesi**: la sezione "Architettura sperimentale" della tesi argomenterà esplicitamente questa scelta contro l'alternativa "1 processo, 4 client LLM".

### 1.3 Wallet Hyperliquid

**Decisione**: 4 wallet Hyperliquid testnet separati, ognuno associato a un sub-wallet MetaMask del wallet principale.

Procedura: 4 nuovi wallet MetaMask → 4 registrazioni Hyperliquid → 4 deposit testnet da 10$ → sblocco 1000$ testnet su ognuno → API key dedicata per ogni wallet.

**Razionale**: l'attribuzione del PnL al modello che ha aperto la posizione diventa banale (`SELECT * FROM ... WHERE wallet_address = X`). Con un wallet condiviso le posizioni dei 4 modelli si mescolerebbero in un solo orderbook account, richiedendo logica di attribuzione artificiosa e fragile.

### 1.4 Design dell'esperimento

**Decisione**: esperimento **comparativo puro a singola condizione di contesto**.

- Variabile indipendente: il modello LLM (4 livelli)
- Variabile costante: contesto, prompt, ticker, capitale iniziale, timing decisionale, network
- Variabili dipendenti misurate: PnL lordo/netto, fee, costi API, latency, drawdown, numero operazioni, decisioni tecniche (direction/leverage/stop_loss), reasoning chain, confidence, reason testuale

**Cron**: ogni 15 minuti i 4 agenti ricevono lo stesso identico contesto e prompt nello stesso identico istante, e producono 4 decisioni indipendenti che vengono eseguite sui rispettivi wallet.

**Durata**: 4 settimane di run continuo dopo approvazione del progetto da parte del professore.

**Razionale**:
- Risponde rigorosamente alle 3 domande di ricerca senza moltiplicare le condizioni.
- Garantisce potenza statistica sufficiente per cella (~2700 osservazioni/modello).
- Tempistiche e complessità sostenibili per il singolo developer-tesista.

### 1.5 Setup ottimizzato dell'agente baseline

**Decisione**: il valore scientifico dell'esperimento dipende dalla **qualità della baseline**. Non si studiano 4 modelli su un agente debole, ma 4 modelli su un agente *competente*.

Le Fasi 2 e 3 (analisi repo avanzata, revisione Figma) servono **a costruire una sola configurazione di agente ben progettata**, replicata su 4 modelli — non 4 configurazioni diverse.

**Principi guida per la composizione del contesto**:
1. Ogni feature aggiunta al contesto deve avere giustificazione documentata (letteratura, pratica nota, evidenza dalla repo avanzata). Niente feature solo perché disponibile.
2. Il contesto totale resta sotto i ~3000-4000 token di input per controllare il costo dei modelli cheap.
3. Aggiungere è facile, togliere è doloroso: si parte snelli e si aggiunge prima del lancio dell'esperimento.

### 1.6 ContextBuilder modulare

**Decisione**: il `ContextBuilder` v2 è progettato in modo **modulare**, anche se per la tesi viene usata una sola configurazione.

Take un dizionario `{news: bool, sentiment: bool, forecasts: bool, indicators: dict, news_count: int, prompt_version: str}` e produce il contesto. Configurato da config file o env vars per ogni servizio. Per la tesi tutti e 4 i servizi avranno la stessa config.

**Razionale**:
- **Future work**: la tesi può chiudere con "il sistema è strumentato per ablation studies che esulano da questa tesi", aprendo a paper o estensioni.
- **Difendibilità**: a una domanda del tipo "perché tutte queste feature?" si risponde "il sistema supporta ablation, la baseline è motivata in §X".
- **Buon design software**: costo marginale di sviluppo basso, valore lungo termine alto.

### 1.7 Network e gestione fondi

**Decisione**: Hyperliquid **testnet**, sempre. Niente mainnet, niente capitale reale.

**Razionale**:
- I dati di mercato testnet sono identici a mainnet (stesso engine, stessi prezzi, stessa volatilità).
- PnL su testnet ≡ PnL della stessa strategia su mainnet, modulo eventuale slippage diverso (residuo).
- Mainnet introduce rischio finanziario senza valore scientifico aggiuntivo.
- Mitiga eventuali perplessità etiche del professore su "studente che fa trading reale come tesi".

### 1.8 Timeline

**Decisione**: qualità prima della velocità, ma con baseline temporale per evitare scope creep.

| Fase | Durata baseline | Estendibile? |
|------|-----------------|--------------|
| Sviluppo agent V2 | 12-15 giorni | Sì, se lo scope tecnico lo richiede |
| Sviluppo dashboard V2 | 7-10 giorni | Sì |
| Presentazione progetto al professore | 1 giorno | — |
| Run sperimentale | 4 settimane fisse | No, è la metrica |
| Analisi dati e scrittura tesi | TBD | — |

Total fino al run: ~3-4 settimane di lavoro concentrato.

---

## 2. Mappa Audit V1 → Decisioni V2

Sintesi delle implicazioni dell'audit (`AUDIT_V1.md`, branch `audit/v1-review`) sulle decisioni di design di V2.

### 2.1 Componenti V1 che entrano in V2 (REUSE / REFACTOR leggero)
- `indicators.py` (CryptoTechnicalAnalysisHL): pattern dual-output (text per LLM + JSON per DB) già scientificamente solido
- Schema DB `account_snapshots`, `open_positions`, `errors`: spina dorsale, da estendere con `experiment_id`/`model_id`/`run_id`
- Pattern execution di `HyperLiquidTrader`: sizing con `Decimal`, SL come trigger reduce_only, validazione robusta
- `system_prompt.txt`: come *baseline versionata* del prompt (`prompt_v1_baseline`)
- `news_feed.py`: fetch RSS pulito, da estendere con normalizzazione persistente per item

### 2.2 Componenti V1 da rifare profondamente (REFACTOR pesante)
- `db_utils.py`: schema da riprogettare attorno a `experiment_id`/`model_id`/`run_id` con FK pre/post snapshot, indicizzazione completa, migrazioni gestite con tooling (Alembic)
- `forecaster.py`: 3 bug strutturali da correggere (parametro `tickers` ignorato, `testnet=True` forzato, bare `except`); seedare la randomness, loggare hyperparameter
- `sentiment.py`: retry/backoff, tipi coerenti, persistenza normalizzata

### 2.3 Componenti V1 da riscrivere da zero (REPLACE)
- `main.py`: orchestratore multi-modello con scheduler interno (APScheduler o equivalente), isolamento errori per modello
- `trading_agent.py`: provider-agnostic con adapter per i 4 modelli, persistenza completa di `response_id`/usage/reasoning/latency
- `utils.check_stop_loss`: query SQL su delta di snapshot, niente filesystem locale

### 2.4 Componenti V1 da rimuovere (REMOVE)
- `whalealert.py`: dead code, parsing fragile, nessuna API ufficiale. Eliminato.

---

## 3. Requisiti tecnici emergenti dalle decisioni

Lista non esaustiva, da espandere in PRD V2.

### 3.1 Schema DB scientifico
Tabelle minime richieste: `experiments`, `models`, `runs`, `decisions`, `account_snapshots`, `open_positions`, `closed_positions`, `model_usage`, `cost_events`, `fee_events`, `tax_simulation_events`, `errors`, `prompts`, `contexts`. Schema completo nel PRD V2 (Fase 4).

### 3.2 LLM Provider abstraction
Interfaccia comune (`LLMProvider`) con 4 implementazioni concrete. Ogni implementazione gestisce: chiamata API, retry/backoff, parsing structured output, estrazione token usage, estrazione reasoning trace (dove disponibile), persistenza completa.

### 3.3 Orchestratore multi-modello
Schedulazione interna (non Railway-restart). Capacità di triggerare il decision-loop per modello in parallelo asincrono. Isolamento errori: un fallimento del modello A non interrompe B/C/D.

### 3.4 Logging strutturato
`logging` standard con formatter JSON e redaction filter per chiavi sensibili. Niente `print` ovunque (debito tecnico V1).

### 3.5 Cost & fee tracking
- Costo API per decisione: `(input_tokens × price_in) + (output_tokens × price_out) + (reasoning_tokens × price_reasoning)` per modello
- Fee Hyperliquid: lettura del `fee` field reale dal response del market order, non stima
- Funding rate: lettura periodica e attribuzione alle posizioni aperte

### 3.6 Tax simulation
Regime italiano: 26% capital gain su PnL realizzato. Tabella dedicata `tax_simulation_events` per ogni chiusura di posizione. Calcolo non distribuisce su lotti FIFO/LIFO in V2 (semplificazione, da motivare).

### 3.7 Sicurezza e secret management
- Secret manager (Railway secrets, NON `.env` committato)
- `WALLET_PRIVATE_KEY` mai loggata, mai serializzata
- News feed sanitization contro prompt injection (rimozione di pattern tipo "ignore previous instructions")
- `git_commit_sha` su ogni `decisions` row per riproducibilità

### 3.8 Reproducibility
- `requirements.txt` con `==` (versioni esatte), o `pyproject.toml` + `uv.lock`
- `seed` deterministico per Prophet e per chiamate LLM (`seed` parameter dove supportato)
- Snapshot del codice (`git_commit_sha`) e del prompt (`prompt_version`) loggato per ogni decisione

---

## 4. Roadmap fino al PRD V2

| Fase | Stato | Output |
|------|-------|--------|
| 0. Congelamento V1 | ✅ DONE | `v1.0.0-beta` taggato su entrambe le repo, audit prodotto |
| 1. Audit V1 | ✅ DONE | `AUDIT_V1.md` su branch `audit/v1-review` |
| 2. Analisi repo avanzata | 🔜 NEXT | `ANALYSIS_REFERENCE_REPO.md` su `prd/v2-design` |
| 3. Revisione Figma | DOPO | `ANALYSIS_FIGMA.md` su `prd/v2-design` |
| 4. Research Design + PRD V2 | DOPO | `RESEARCH_DESIGN.md` + `PRD_V2.md` |
| 5. Implementazione agent V2 | DOPO | nuovo branch `v2/develop` da `main` |
| 6. Implementazione dashboard V2 | DOPO | repo dashboard, branch `v2/develop` |
| 7. Run sperimentale | DOPO | 4 settimane di dati su DB |

---

## 5. Decisioni ancora aperte

Da risolvere in Fase 4 (PRD V2):

1. Scelta finale dei 4 modelli LLM specifici (post verifica pricing/disponibilità)
2. Schema DB esatto (DDL completo)
3. Strategia di migrazione DB (Alembic vs Yoyo vs custom)
4. Risk management policy (max leverage, max % portfolio per trade, max drawdown trigger di stop)
5. Ticker finali (3 o più; baseline V2 sarà BTC/ETH/SOL come V1)
6. Scheduler library (APScheduler vs Celery beat vs asyncio loop custom)
7. Strategia di retry/backoff (tenacity? custom?)
8. Frontend stack della dashboard V2 (decisione in Fase 3 con design Claude)

---

## 6. Glossario di progetto

- **decision**: una singola invocazione del decision-loop di un modello, in un istante di tempo, su un set di ticker. Genera N output strutturati (uno per ticker se trade, oppure decisione di non operare).
- **run**: un'esecuzione del cron di 15 minuti su un singolo modello. Una run contiene N decisions.
- **experiment**: una configurazione completa (4 modelli, contesto, prompt, ticker, capitale iniziale, durata). La tesi avrà un singolo experiment principale.
- **context**: l'insieme dei dati passati al modello (indicators + news + sentiment + forecasts + portfolio_state).
- **model_id**: identificatore stringa univoco di un modello (es. `openai-gpt-5.1`, `anthropic-claude-sonnet-4.6`, `deepseek-r1`, `qwen-2.5-max`).
- **run_id / decision_id**: UUID o monotonic ID per tracciamento.
- **prompt_version**: SHA o versione semantica del system prompt.
- **git_commit_sha**: SHA del commit del codice agent al momento della decisione.

---

*Fine PRE_PRD. Prossimo documento: `ANALYSIS_REFERENCE_REPO.md` (Fase 2).*