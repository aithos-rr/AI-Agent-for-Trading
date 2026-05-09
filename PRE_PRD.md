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

## 11. Decisioni emerse dall'analisi della reference repo (Fase 2)

> Sezione aggiunta dopo l'analisi di `ANALYSIS_REFERENCE_REPO.md` (commit `df2d87b`).
> Le decisioni qui consolidate **estendono e raffinano** le 6 strategiche di §1, senza modificarle. Hanno priorità nel PRD V2.

### 11.1 Stack LLM — `langchain-core` minimo

**Decisione**: adottare `langchain-core` + `langchain-openai` + `langchain-anthropic` come strato di astrazione provider, **senza** LangGraph, **senza** LangChain alto livello, **senza** agent toolkits.

Funzione esposta principale: `with_structured_output(schema)` per output Pydantic. Pattern replicato da `tradingagents/agents/utils/structured.py` (`bind_structured` + fallback freetext).

**Razionale**:
1. Riproducibilità — pattern stabile, validato in produzione su 6 provider in TradingAgents
2. Coverage — i 4 modelli scelti (OpenAI, Anthropic, DeepSeek, Qwen) sono tutti supportati nativamente; DeepSeek/Qwen via `langchain-openai` con `base_url` custom
3. Stabilità — community ampia, documentazione estesa per troubleshooting durante i 4 settimane di run
4. Footprint minimo — usiamo il 5% del framework, non paghiamo il 95% restante

**Trade-off accettato**: si porta come dipendenza un ecosistema più pesante di alternative come `instructor` o `pydantic-ai`, in cambio di stabilità e coverage immediati.

### 11.2 Structured output Pydantic con fallback freetext (REQUISITO SCIENTIFICO)

**Decisione**: ogni decisione del modello deve essere validata contro uno schema Pydantic via `with_structured_output`. Se la chiamata strutturata fallisce (es. DeepSeek-R1 senza tool_choice, Qwen/GLM erratici, context window superato), il sistema ritenta **una volta** in modalità freetext con regex-parsing minimale.

**Razionale**: è un requisito di **integrità del dataset**, non un'ottimizzazione. Senza fallback, una decisione mancata = un buco sistematico nel dataset di tesi → osservazioni mancanti correlate al modello (specialmente cinesi) → bias non controllato nelle analisi cross-model. **Inaccettabile per la tesi.**

### 11.3 Context window strategy — zero decision history nel prompt

**Decisione**: il prompt del modello include sempre **Memoria 1** (portfolio state corrente: balance, posizioni aperte, PnL non realizzato, leverage, SL/TP attivi) e **NON** include **Memoria 2** (storico delle proprie decisioni passate).

L'infrastruttura `MemoryRepo` viene comunque costruita su Postgres con interfacce `insert_pending(decision)` e `resolve_outcomes(model_id, before_ts)`, ma il `ContextBuilder` ha flag `inject_decision_history: bool = False` di default, che resta False per tutto l'esperimento di tesi.

**Razionale scientifico**: la tesi misura il comportamento del modello a parità di stato di mercato e portafoglio, **non** l'effetto cumulativo di self-learning intra-esperimento. Iniettare decisioni passate confonde la variabile "modello" con la variabile "memoria della propria storia". L'opzione A (zero history) è più pulita scientificamente:
- isola la variabile modello
- rende le 4 settimane di osservazioni i.i.d. condizionatamente al mercato
- è difendibile in tesi: *"abbiamo isolato la variabile modello escludendo l'in-context learning sulla propria storia, perché lo studio mira a misurare il comportamento del modello a parità di stato — non l'effetto cumulativo di self-learning."*

**Future work**: una tesi magistrale o un paper può attivare il flag `inject_decision_history=True` e fare un confronto sistematico A/B.

### 11.4 Composizione del prompt — 4 sezioni tematiche

**Decisione**: il prompt del modello si articola in 4 sezioni tematiche fisse, popolate da *collector deterministici* (no LLM, dati fetched a monte e formattati come testo):

1. `## Technical` — indicatori tecnici (EMA, MACD, RSI, Bollinger, ATR, VWMA, pivot daily) calcolati su candele Hyperliquid 15m + longer-term context
2. `## Sentiment` — Fear & Greed Index + (eventuale) LunarCrush/Santiment se accessibili
3. `## News & Macro` — RSS CryptoPanic / CoinDesk / coinjournal
4. `## On-chain & Funding` — funding rate Hyperliquid, open interest, liquidations recenti, basis perp-spot, orderbook depth

**Razionale**: replica la struttura "4 analyst views" di TradingAgents, ma collassata in 4 sezioni del **singolo prompt** (non 4 sotto-agenti). La sezione "Fundamentals" del modello originale (10-K, balance sheet) è rimpiazzata da "On-chain & Funding" perché inapplicabile a crypto perpetuals.

I collector deterministici evitano LLM-call multipli in fase di costruzione contesto: una sola LLM-call per decisione, basso costo e bassa latency.

### 11.5 Outcome benchmark — quadrupletta PnL + buy-and-hold spot

**Decisione**: ogni decisione/esito viene misurato lungo 4 metriche cumulative, tutte persistite in tabella `outcomes`:

1. **PnL lordo** — variazione di equity senza considerare costi
2. **PnL netto-fee** — sottratti taker/maker fee Hyperliquid
3. **PnL netto-fee-funding** — sottratto anche funding rate pagato/ricevuto su posizioni aperte
4. **PnL netto-fee-funding-tax** — sottratta anche simulazione tasse italiane (26% capital gain)

Benchmark di riferimento: **buy-and-hold spot** dello stesso symbol nello stesso periodo, calcolato a posteriori sul DB.

**Razionale**: la dimensione "fattibilità" della tesi è ambigua senza la quadrupletta — un PnL +5% lordo può diventare -2% post-tax. Il benchmark buy-and-hold sostituisce l'`alpha-vs-SPY` di TradingAgents (irrilevante per crypto retail) con un riferimento naturale: *"il modello batte il semplice hold del sottostante?"*.

### 11.6 Prompt versioning — SHA256 hash committed

**Decisione**: il prompt template è memorizzato in file (`prompts/v2_decision.md` o equivalente). Ad ogni run il sistema calcola SHA256 del prompt finalizzato (template + variabili interpolate, escluso il portfolio state che cambia per definizione) e lo salva in `runs.prompt_version`.

**Razionale**:
- Riproducibilità rigorosa: a esperimento concluso, ogni decisione è ricostruibile dal commit del codice + hash del prompt
- Permette eventuale evoluzione del prompt durante l'esperimento (es. *"settimane 1-2 con prompt v1, settimane 3-4 con prompt v2 dopo aver osservato pattern X"*) con tracciabilità completa
- Tesi: si può scrivere *"l'esperimento ha usato N versioni del prompt, identificate da hash, dettagliate in §X"*

### 11.7 Cost ledger — `model_pricing.yaml`

**Decisione**: file YAML con pricing per modello (input + output + reasoning $/1M token), aggiornato manualmente, usato dal cost-tracker per scrivere `decisions.cost_usd` ad ogni invocazione.

Esempio struttura:
```yaml
openai-gpt-5.1:
  input_per_1m: 5.0
  output_per_1m: 15.0
  reasoning_per_1m: 60.0
deepseek-r1:
  input_per_1m: 0.55
  output_per_1m: 2.19
  reasoning_per_1m: 0
# ...
```

**Razionale**: la dimensione "fattibilità costi" della tesi richiede `cost_usd` per decisione persistito **al momento della decisione**, non calcolato ex-post da log esterni (che soffrono di provenance drift, modifiche dei pricing, downtime di dashboard provider).

### 11.8 Test di non-contaminazione cross-model

**Decisione**: la suite di test include `tests/test_isolation.py` che verifica:
- Un servizio Railway con `model_id=A` non legge mai decisioni di altri `model_id` durante la propria run
- Le query del `MemoryRepo` (anche con `inject_decision_history=False` di default) sono filtrate sempre per `WHERE model_id = $1`
- Non c'è cross-contamination accidentale via cache, file system condiviso, log condivisi

**Razionale**: l'isolamento sperimentale è il **prerequisito** per affermare scientificamente che differenze osservate tra modelli sono attribuibili al modello stesso, non a contaminazioni dell'orchestrazione. Va testato esplicitamente, non assunto.

### 11.9 Schema TradeDecision (output del modello) — proposta consolidata

**Decisione**: lo schema Pydantic dell'output strutturato del modello include, oltre a quanto già previsto in V1:

```python
class Side(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"  # chiudi posizioni esistenti e resta out
    HOLD = "hold"  # mantieni stato attuale, nessuna azione

class TradeDecision(BaseModel):
    side: Side
    confidence: float = Field(ge=0.0, le=1.0)
    leverage: float = Field(ge=1.0, le=10.0, default=1.0)
    size_pct_equity: float = Field(ge=0.0, le=1.0)
    entry_type: Literal["market", "limit"] = "market"
    limit_price: Optional[float] = None
    stop_loss_pct: Optional[float] = Field(None, ge=0.001, le=0.20)
    take_profit_pct: Optional[float] = Field(None, ge=0.001, le=0.50)
    time_horizon_min: int = Field(ge=15, le=10080)
    reasoning: str
    key_signals: list[str]
    risk_assessment: str
```

**Razionale**:
- `confidence` necessaria per la dimensione "spiegabilità" — emessa per ogni decisione, non solo aperture
- `key_signals: list[str]` permette analisi "quali segnali ciascun modello cita più spesso?" cross-model
- `risk_assessment` è il sostituto compatto del Risk Manager separato di TradingAgents
- `Side.HOLD` distinto da `Side.FLAT` perché semanticamente diversi: HOLD = nulla cambia, FLAT = chiudi tutto e stai out

Schema definitivo (con eventuali ulteriori campi) sarà fissato nel PRD V2.

---

## 12. Sintesi consolidata delle decisioni totali

A questo punto il PRE_PRD ha cristallizzato **15 decisioni** complessive:
- §1 — 8 decisioni strategiche originali (modelli, architettura, wallet, esperimento, baseline competente, ContextBuilder modulare, network, timeline)
- §11 — 9 decisioni emerse dall'analisi reference repo + memoria (stack LLM, structured output, context window strategy, prompt sezioni, outcome benchmark, prompt versioning, cost ledger, isolation test, TradeDecision schema)

Le decisioni di §1 vincolano il *cosa* della tesi. Le decisioni di §11 vincolano il *come* implementativo difendibile scientificamente.

Roadmap aggiornata: PRE_PRD ✅ DONE. Prossimo passo: revisione Figma (Fase 3), poi PRD V2 (Fase 4) che ingloba tutto.

---

## 13. Decisioni emerse dall'analisi del Figma (Fase 3)

> Sezione aggiunta dopo l'analisi di `ANALYSIS_FIGMA.md` (commit `42dbb3d`).
> Il Figma originale era una mind map architetturale, non un mockup UI. Da esso sono emersi 3 requisiti operativi che, formalizzati, compongono la **strategia di risk management integrata** del sistema.

### 13.1 Stop Loss e Take Profit obbligatori (F1)

**Decisione**: lo schema `TradeDecision` (vedi §11.9) viene reso conditionally required:
- Se `side ∈ {LONG, SHORT}`: `stop_loss_pct` e `take_profit_pct` sono obbligatori
- Se `side ∈ {FLAT, HOLD}`: i due campi sono `null`

Implementabile con `model_validator` Pydantic post-validation, oppure con due schemi distinti (`OpenDecision` vs `CloseOrHoldDecision`) e `Union` discriminato su `side`. Decisione finale tra le due opzioni rimandata al PRD V2.

**Razionale**: senza SL il rischio liquidation è reale anche su testnet 1000$. Per la tesi un modello che brucia il capitale in pochi giorni non genera dati di "fattibilità" ma di "fallimento operativo". SL obbligatorio garantisce continuità sperimentale a 4 settimane per tutti i 4 modelli.

### 13.2 Confidence obbligatoria sempre (F2)

**Decisione**: `decisions.confidence` è `NOT NULL` per ogni decisione, inclusi `HOLD` e `FLAT`. Non solo aperture e chiusure.

**Razionale**: la confidence con cui un modello *decide di non operare* è dato scientifico utile per la dimensione "spiegabilità". Un HOLD a confidence 0.95 è epistemicamente diverso da un HOLD a confidence 0.30. Senza questo dato, l'analisi della distribuzione di confidence cross-model è troncata sui soli trade attivi.

### 13.3 Strategia C+ — 4 guardrail di risk management (F3)

**Decisione**: il sistema impone 4 vincoli operativi che limitano *l'esecuzione*, non la *decisione* del modello. Il modello è libero di proporre qualunque combinazione di parametri; l'execution layer applica i seguenti clamp e ne logga l'attivazione:

| # | Guardrail | Regola | Env-var (default) |
|---|-----------|--------|-------------------|
| 1 | SL mandatory | Vedi §13.1 | — |
| 2 | Exposure cap per trade | `size_pct_equity ≤ MAX_SIZE_PCT` | `AIAT_MAX_SIZE_PCT=0.20` |
| 3 | Leverage cap dinamico | `leverage ≤ 1 + confidence × 9`, hard cap | `AIAT_HARD_MAX_LEVERAGE=10` |
| 4 | Confidence threshold per aperture | Se `confidence < MIN_OPEN_CONFIDENCE` e `side ∈ {LONG,SHORT}` → forza `HOLD` | `AIAT_MIN_OPEN_CONFIDENCE=0.4` |

**Razionale per ciascun guardrail**:
- **#2** Limita l'esposizione singola a 20% equity → max 5 posizioni concorrenti per modello → preserva diversificazione e sopravvivenza statistica del capitale
- **#3** Lega rischio richiesto a fiducia dichiarata: il modello *può* avere alta leva solo se è anche molto sicuro. Crea correlazione **forzata** tra leverage e confidence che diventa essa stessa oggetto di analisi (i 4 modelli rispettano spontaneamente questa correlazione, o vengono spesso clampati?)
- **#4** Sotto-soglia il modello non opera. Guardrail post-decisione, non vincolo nel prompt — il modello rimane libero di richiedere apertura con bassa confidence; il sistema non esegue. La frequenza di `forced_hold` per ciascun modello è una metrica di tesi.

**Riepilogo formale (per la tesi)**:

> *"Al modello sono stati imposti quattro guardrail di prudenza operativa: (1) stop loss obbligatorio per ogni apertura, (2) esposizione massima per trade del 20% dell'equity, (3) leva massima funzione lineare della confidence dichiarata (formula 1 + c·9, hard cap 10x), (4) soglia minima di confidence 0.4 per aperture. Tutti gli altri parametri decisionali (direzione, size esatto entro il cap, durata, prezzo limit, eventuali campi opzionali) sono lasciati alla discrezione del modello. Le attivazioni dei guardrail sono persistite per analisi cross-model a posteriori."*

### 13.4 Estensione schema DB per analisi guardrail

Le tabelle del PRD V2 dovranno includere campi aggiuntivi per tracciamento:

| Campo | Tipo | Significato |
|-------|------|-------------|
| `decisions.leverage_requested` | NUMERIC | Quanto chiedeva il modello |
| `decisions.leverage_executed` | NUMERIC | Cosa è stato effettivamente eseguito post-clamping |
| `decisions.leverage_clamped` | BOOL | Guardrail #3 attivato? |
| `decisions.size_pct_requested` | NUMERIC | Quanto chiedeva il modello |
| `decisions.size_pct_executed` | NUMERIC | Eseguito post-clamping |
| `decisions.size_pct_clamped` | BOOL | Guardrail #2 attivato? |
| `decisions.forced_hold` | BOOL | Guardrail #4 attivato? |
| `decisions.original_side` | TEXT | Side richiesto dal modello, se sostituito |

Servono per analisi cross-model: *"quanto spesso ciascun modello chiede leva oltre il consentito? Lo fa sistematicamente o sporadicamente? Le richieste oltre cap correlano con confidence alta o bassa?"* — domande direttamente collegate alla dimensione "spiegabilità" e "proprietà emergenti".

---

## 14. Sintesi consolidata delle decisioni totali (aggiornata Fase 3)

Il PRE_PRD ha cristallizzato **18 decisioni** complessive:
- §1 — 8 decisioni strategiche originali
- §11 — 9 decisioni emerse dall'analisi reference repo + memoria
- §13 — 3 requisiti formalizzati dal Figma + 1 strategia integrata di risk management (4 guardrail) + 1 estensione schema DB

Il PRE_PRD è ora completo. Da qui si passa alla Fase 4 con 2 documenti:
- `RESEARCH_DESIGN.md` — la cornice scientifica della tesi (3 ipotesi, variabili, metriche, design statistico, ipotesi sulle conclusioni attese)
- `PRD_V2.md` — il documento tecnico-implementativo (architettura, schema DB completo, API tra moduli, contratti LLM, timeline e milestone)

---

*Fine PRE_PRD aggiornato a Fase 3. Prossimi documenti: `RESEARCH_DESIGN.md` + `PRD_V2.md` (Fase 4).*