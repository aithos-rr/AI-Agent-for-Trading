# Research Design — V2 Thesis Edition

> Documento scientifico della tesi. Definisce le domande di ricerca, le ipotesi testabili, le variabili, l'operazionalizzazione delle metriche, le aspettative pre-registrate e le limitazioni del design.
> Input: le 18 decisioni del `PRE_PRD.md` (commit `73e3d02`).
> Output: cornice scientifica che il PRD V2 implementa fedelmente.
> Branch: `prd/v2-design`. Da leggere PRIMA del PRD V2.

---

## 0. Cornice della tesi

**Titolo provvisorio**: *Comportamento decisionale di Large Language Models in un dominio finanziario ad alta variabilità: studio comparativo cross-model su crypto-perpetuals.*

**Disciplina**: tesi triennale in *Filosofia e Intelligenza Artificiale*, Sapienza.

**Natura del lavoro**: indagine empirica con design comparativo controllato. Non è una tesi di ottimizzazione finanziaria. Lo scopo non è "fare profitto" ma "**produrre evidenza** sul comportamento di LLM diversi posti nelle stesse identiche condizioni decisionali".

**Claim centrale (tesi difendibile)**:

> *In un ambiente controllato di decisione finanziaria sequenziale, diversi LLM mostrano profili decisionali, economici e giustificativi misurabilmente differenti, osservabili attraverso un dataset originale e un protocollo sperimentale riproducibile.*

Questa formulazione è **deliberatamente sobria**. Non rivendica:
- causalità tra training data e comportamento osservato
- "model-attribuibilità" in senso forte (richiederebbe ablation su training, fuori scope)
- intercambiabilità o gerarchia di "intelligenza" tra modelli

Rivendica invece osservabilità empirica di **differenze associate al modello sotto condizioni controllate di prompt e contesto** (in inglese: *model-associated under controlled prompt and context conditions*). Questa cautela linguistica è applicata in tutto il documento.

**Contributo atteso alla letteratura**: dataset originale di osservazioni decisionali (cifra esatta in §1.0 una volta chiarita l'unità di osservazione) con metriche complete su fattibilità economica, coerenza giustificativa, e profili comportamentali misurabili. Il dataset è il deliverable scientifico primario; le analisi presentate in tesi sono un primo strato di lettura, non l'unico possibile.

---

## 1. Domande di ricerca formalizzate

### 1.0 Unità di osservazione

Prima di formalizzare le RQ è necessario chiarire **cosa conta come una "osservazione"** nel disegno sperimentale. Questa scelta vincola schema DB, statistiche, conteggi del dataset e potenza statistica.

**Decisione**: l'unità di osservazione decisionale è la coppia `(timestamp, model_id)`, NON la tripla `(timestamp, model_id, symbol)`.

**Significato operativo**: ad ogni cron-tick (ogni 15 minuti) ciascun modello produce **una sola chiamata LLM** il cui output JSON contiene azioni per *tutti* i simboli osservati (BTC, ETH, SOL). Il modello decide olisticamente sul portafoglio, non simbolo per simbolo. Una singola decisione può includere ad esempio: `{actions: [{symbol: "BTC", side: "LONG", ...}, {symbol: "ETH", side: "HOLD", ...}, {symbol: "SOL", side: "FLAT", ...}]}`.

**Razionale**:
1. **Coerenza decisionale**: il modello può ragionare su correlazioni e bilanciamenti (es. ridurre esposizione BTC perché si sta aprendo ETH), comportamento naturale per un trader umano.
2. **Costo API contenuto**: 1 chiamata LLM/tick invece di 3, riduce di ~66% input tokens e latency.
3. **Sincronizzazione cross-symbol**: tutte le azioni di un tick condividono lo stesso stato di mercato (no race condition tra simboli).
4. **Semplificazione statistica**: l'unità i.i.d. (sotto le riserve di §6.1) è più chiaramente definita.

**Conseguenze sul dataset count**:
- Decisioni totali: 4 modelli × 4 settimane × 7 giorni × 24 ore × 4 cron/h = **10.752 decisioni** (vicino al "~11k" dichiarato)
- Azioni totali (decisione × simbolo): 10.752 × 3 = **32.256 azioni elementari**
- Trade chiusi attesi: 800-1500 per modello (dipendente da frequenza HOLD)

Tabelle DB derivate: `decisions(decision_id, timestamp, model_id, ...)` come tabella padre, `decision_actions(action_id, decision_id, symbol, side, leverage, ...)` come figlia 1-to-many.

### 1.1 Le tre Research Questions

Le tre dimensioni dichiarate (fattibilità, spiegabilità dichiarata, profili comportamentali emergenti) si traducono in tre Research Questions (RQ) testabili.

### RQ1 — Fattibilità

> *In che misura un agente LLM può operare in modo economicamente sostenibile sul mercato di crypto-perpetuals quando i suoi costi operativi (fee di exchange, funding rate, costi API) e una simulazione fiscale controfattuale basata sull'aliquota italiana sui capital gain sono inclusi nel calcolo della performance?*

**Sottoquestioni operative**:
- RQ1.1 — Il PnL netto post-fee, post-funding, post-tax-simulation è positivo per almeno uno dei 4 modelli su 4 settimane, **e supera un baseline non-LLM nello stesso periodo**?
- RQ1.2 — Esistono modelli che, pur generando PnL lordo positivo, diventano non-fattibili dopo l'inclusione dei costi API (frontier "gross-positive, net-negative")?
- RQ1.3 — La latency delle decisioni (prompt-to-execution) impatta sistematicamente sulla performance? Esiste correlazione tra latency e PnL netto?
- RQ1.4 — Quali metriche di rischio finanziario standard (Sharpe, Sortino, max drawdown, exposure time, turnover, fee-to-PnL ratio) differiscono significativamente tra modelli? La performance va valutata in joint con il rischio assunto per ottenerla, non solo come PnL puntuale.

**Nota terminologica**: la "simulazione fiscale" è un calcolo controfattuale a posteriori (sottrazione del 26% sul PnL realizzato positivo per finestre trimestrali, con compensazione perdite/profitti come da regime italiano semplificato). Non costituisce consulenza fiscale né corrisponde a un'eventuale fiscalità reale che sarebbe più complessa. È una metrica sperimentale, non un calcolo legale.

### RQ2 — Spiegabilità dichiarata e coerenza giustificativa

> *Le decisioni prodotte da LLM diversi nelle stesse condizioni di mercato sono ricostruibili a posteriori in modo coerente? La qualità delle spiegazioni dichiarate è confrontabile cross-model? Esistono profili di self-explanation sistematicamente diversi tra modelli?*

**Distinzione epistemologica fondamentale** (importante per una tesi in Filosofia e IA):

quando un LLM produce un campo `reasoning` insieme alla decisione, ciò che osserviamo è una **self-explanation** (giustificazione narrativa post-hoc generata dal modello), NON necessariamente il vero processo causale interno che ha portato alla decisione. La letteratura sull'interpretability degli LLM ha mostrato ampiamente che le razionalizzazioni linguistiche dei modelli possono divergere dai meccanismi computazionali sottostanti (*post-hoc rationalization gap*).

Per questo, RQ2 si articola in **due livelli**:

**Livello 2.A — Coerenza interna (scope di questa tesi, misurabile)**:
Il `reasoning` testuale, l'`action` decisa, la `confidence` dichiarata e i `key_signals` citati sono internamente coerenti? Si contraddicono? Citano elementi presenti nel contesto?

**Livello 2.B — Fedeltà causale (fuori scope, dichiarato)**:
Il `reasoning` rappresenta il vero processo causale interno del modello? Questa domanda richiederebbe *mechanistic interpretability*, accesso ai weights, ablation neuronale. **Esplicitamente fuori scope** di una tesi triennale che lavora con modelli closed-source via API.

La tesi può quindi affermare cose come *"il modello A produce reasoning più dettagliato del modello B"* o *"la coerenza interna varia sistematicamente tra modelli"*, ma NON *"il modello A spiega davvero il proprio processo decisionale meglio del modello B"*. Distinzione decisiva.

**Sottoquestioni operative (Livello 2.A — Coerenza interna)**:
- RQ2.1 — La coerenza tra `reasoning` testuale (giustificazione narrativa) e `key_signals` (segnali strutturati citati) è uniforme tra modelli o varia sistematicamente?
- RQ2.2 — La confidence dichiarata è calibrata? (decisioni con confidence > 0.7 hanno tasso di successo significativamente superiore a confidence < 0.4?). Per definizione operativa di confidence, vedi §2.
- RQ2.3 — La distribuzione di confidence è simile tra modelli o esistono pattern di sovra/sotto-confidenza per famiglia di modelli?
- RQ2.4 — I `key_signals` citati come decisivi differiscono sistematicamente tra modelli a parità di contesto? (Per validità della comparazione, i signals saranno estratti da un **vocabolario controllato**, vedi §3.3).

### RQ3 — Profili comportamentali associati al modello

> *A parità di prompt, di stato di portafoglio e di contesto di mercato, i 4 LLM sviluppano profili comportamentali sistematicamente diversi — e queste differenze sono **associate al modello** nella configurazione sperimentale adottata, in modo che possano essere documentate empiricamente come oggetto di osservazione (non come effetto causale dimostrato)?*

**Nota terminologica importante**: il termine "model-attribuibile" è stato deliberatamente sostituito con "model-associated under controlled prompt and context conditions" in tutto il documento. La differenza è epistemologicamente significativa:
- *Attribuibile* implicherebbe causalità (training data, RLHF, scala → comportamento). Affermazione che richiederebbe ablation experimentale impossibile con modelli closed-source.
- *Associated* implica solo correlazione osservata sotto condizioni controllate. Affermazione difendibile con il design adottato.

**Sottoquestioni operative**:
- RQ3.1 — Coefficiente di accordo decisionale (Cohen's kappa o Fleiss' kappa[^kappa]) tra i 4 modelli sulle stesse condizioni di mercato: gli LLM concordano o divergono nelle loro decisioni?
- RQ3.2 — Stili di trading osservabili: numero medio di posizioni aperte, leverage medio richiesto, durata media delle posizioni, frequenza di clamping dei guardrail. Sono differenze associate al modello?
- RQ3.3 — Reazione a regimi di mercato: in giorni di alta volatilità i modelli convergono o divergono? In giorni laterali?
- RQ3.4 — *Analisi esplorativa secondaria*: i pattern osservati correlano con la dimensione USA/CN o premium/cheap? Con 4 modelli (1 per cella del 2×2) questo confronto è **suggerimento descrittivo, non test statistico conclusivo**.
- RQ3.5 — *Analisi interpretativa speculativa (sezione Discussione, non Risultati)*: i pattern comportamentali osservati sono coerenti con ipotesi narrative sui dataset di training delle aziende produttrici? (Es. DeepSeek e l'origine quantitativa di High-Flyer, esposizione a dati finanziari/bot trading; OpenAI e dati Western; Anthropic e filtri RLHF su consigli rischiosi; Qwen/Kimi e cosmologia di mercato cinese.) Queste ipotesi sono **speculative interpretative, non causalità verificabili**: vanno trattate nel capitolo Discussione filosofica, non nei risultati statistici.

---

## 2. Ipotesi testabili e definizioni operative

### 2.1 Definizione operativa di confidence (vincolante nel prompt)

Prima di formalizzare le ipotesi su confidence, è necessaria una definizione operativa esplicita che il prompt impone a tutti i 4 modelli, identicamente. Senza questo vincolo, ogni modello potrebbe interpretare "confidence" in modo diverso (sicurezza narrativa, forza del segnale, probabilità di profitto, ...), invalidando ogni confronto cross-model e ogni misurazione di calibrazione (Brier score).

**Definizione vincolata nel prompt** (formulazione esatta, da fissare in PRD V2):

> *"`confidence`: estimated probability ∈ [0, 1] that this specific action will produce positive net PnL (after fees and funding) within the chosen `time_horizon_min`. For `side ∈ {HOLD, FLAT}`, `confidence` represents the estimated probability that this passive choice is preferable to the active alternatives at this moment."*

Questa definizione:
1. Esplicita che confidence è una **probabilità di outcome netto positivo**, non una sicurezza narrativa
2. Ancora la confidence a una finestra temporale dichiarata (`time_horizon_min` dello schema)
3. Estende la nozione a HOLD/FLAT, mantenendo significato uniforme per tutti i side
4. Permette il calcolo del Brier score su tutte le decisioni con outcome osservabile (entro il time_horizon dichiarato)

**Specifica differita al PRD V2**: la regola operativa di labeling per `outcome_i` di HOLD/FLAT richiede definizione controfattuale rigorosa (preferibile rispetto a *quale* alternativa? Migliore tra LONG/SHORT? Migliore rispetto alla media delle alternative? Migliore rispetto alla migliore alternativa ex-post?). Il Research Design fissa la definizione concettuale di confidence; il PRD V2 fisserà la regola di labeling univoca con scelta motivata tra le alternative ex-post possibili.

### 2.2 Ipotesi formali (H0/H1 per ciascuna RQ)

Per ciascuna RQ, ipotesi nulla (H0) e alternativa (H1) pre-registrate. Notazione: `Hx_RQy` dove `x ∈ {0, 1}` (nulla/alternativa) e `y ∈ {1, 2, 3}` (RQ).

#### Ipotesi RQ1 — Fattibilità

- **H0_RQ1**: tutti i 4 modelli producono PnL netto post-tax-simulation non statisticamente diverso da zero su 4 settimane, e nessun modello supera significativamente i baseline non-LLM.
- **H1_RQ1**: almeno un modello produce PnL netto post-tax-simulation positivo statisticamente significativo, **e** supera significativamente almeno uno dei baseline non-LLM.

#### Ipotesi RQ2 — Spiegabilità dichiarata e coerenza giustificativa

- **H0_RQ2**: la coerenza interna delle decisioni (reasoning↔action, confidence↔success-rate, key_signals↔contesto) è statisticamente equivalente tra i 4 modelli.
- **H1_RQ2**: i 4 modelli mostrano profili di coerenza interna sistematicamente diversi, associati al modello (effect size > soglia su almeno 2 metriche di coerenza su 4).

#### Ipotesi RQ3 — Profili comportamentali associati al modello

- **H0_RQ3**: a parità di stato (mercato + portafoglio + prompt), le decisioni dei 4 modelli sono indipendenti tra loro al netto della correlazione attesa per chance (kappa[^kappa] ≈ 0).
- **H1_RQ3**: a parità di stato, esistono profili comportamentali associati al modello statisticamente significativi (almeno 3 metriche su 6 con effect size rilevante per almeno una coppia di modelli).

[^kappa]: *Cohen's kappa* e *Fleiss' kappa* sono misure standard di accordo tra giudici indipendenti che classificano gli stessi item. Cohen's per 2 giudici, Fleiss' per N. Range: -1 (disaccordo totale) a +1 (accordo perfetto), con 0 = accordo casuale. Nel nostro caso: ad ogni cron-tick i 4 modelli ricevono lo stesso identico contesto e producono 4 decisioni; calcoliamo Fleiss' kappa sull'insieme delle decisioni. Valore basso → modelli decidono come 4 giudici indipendenti; valore alto → modelli quasi intercambiabili. Il valore stesso di kappa è un risultato scientifico.

**Importante**: le tre H1 sono *aspettative*, non *predizioni*. Lo studio è descrittivo-comparativo. L'esito "tutte le H0 confermate" è un risultato scientifico legittimo, perché significherebbe che i 4 modelli sono *funzionalmente intercambiabili* in questo dominio sotto le condizioni testate — affermazione di valore non banale.

---

## 3. Variabili dell'esperimento

### 3.1 Variabili indipendenti (manipolate dal disegno)

| Variabile | Livelli | Note |
|-----------|---------|------|
| **Modello LLM** | 4 livelli (selezione finale in PRD V2) | Variabile principale, design 2×2 USA/CN × premium/cheap-alt |
| Wallet Hyperliquid | 4 wallet distinti, 1 per modello | Garantisce isolamento PnL e attribuzione deterministica |
| Capitale iniziale | 1000$ testnet × 4 | Costante tra modelli |

### 3.2 Variabili costanti (controllate)

| Variabile | Valore |
|-----------|--------|
| Prompt template | Versione unica, hash SHA256[^sha] in `runs.prompt_version` |
| Composizione contesto | 4 sezioni fisse (Technical / Sentiment / News & Macro / On-chain & Funding) |
| Cron interval | 15 minuti, sincronizzato cross-modello |
| Network | Hyperliquid testnet (sempre) |
| Ticker iniziali | BTC, ETH, SOL (set comune ai 4 modelli) |
| Memoria 2 (decision history) | OFF di default per tutti i modelli |
| Guardrail di risk management | Identici per i 4 modelli (4 guardrail di Strategia C+) |

[^sha]: *SHA256* è una funzione hash crittografica che produce un'impronta digitale fissa di 64 caratteri esadecimali da qualunque testo in input. Anche un solo carattere modificato genera un hash completamente diverso. Salvando il SHA256 del prompt in DB, possiamo verificare a posteriori con certezza assoluta se due decisioni sono state prodotte con lo stesso prompt o con prompt diversi.

### 3.3 Variabili dipendenti (osservate e misurate)

Suddivise per RQ. Le metriche segnate con (*) sono aggiunte rispetto alla versione iniziale del documento, in seguito alla review esterna.

**Per RQ1 (Fattibilità) — performance economica**:
- `PnL_lordo` per modello (cumulato e per finestra temporale)
- `PnL_netto-fee` 
- `PnL_netto-fee-funding`
- `PnL_netto-fee-funding-tax-sim` (post 26% simulazione fiscale ITA)
- `cost_usd_cumulative` per modello (token in/out × pricing)
- `latency_ms` per decisione (distribuzione)
- `n_decisions_failed` (decisioni andate in fallback freetext, non eseguite)
- `gross_to_net_ratio` per modello
- `fee_to_pnl_ratio` per modello (*)
- `cost_api_to_pnl_ratio` per modello (*)

**Per RQ1 (Fattibilità) — metriche di rischio finanziario** (*):
Standard nella letteratura StockBench/quantitative finance, riportate per ogni modello e per i baseline:
- `max_drawdown_pct`: massima perdita cumulata dal picco precedente
- `sharpe_ratio` (annualizzato, risk-free rate = 0 per testnet)
- `sortino_ratio` (penalizza solo volatilità al ribasso)
- `win_rate`: frazione di trade chiusi in profit
- `average_win_pct` / `average_loss_pct`
- `profit_factor`: total wins / total losses (assoluti)
- `exposure_time_pct`: % di tempo con posizioni aperte
- `turnover`: volume cumulato eseguito / capitale medio

**Baseline obbligatori per RQ1** (*): la performance dei modelli è significativa solo se confrontata con riferimenti non-LLM. Tre baseline calcolati a posteriori sullo stesso periodo, con **parametri pre-registrati ora** (vincolanti per la pre-registrazione scientifica):

1. **Buy-and-Hold spot**: capitale equidistribuito al t=0 — 333.33$ ciascuno su BTC/ETH/SOL spot, no rebalancing, no fee (assunzione: replicabile su exchange spot a fee trascurabile per il periodo), valutato a t=4 settimane.

2. **Cash / No-Trade**: capitale fermo 1000$, PnL = 0, costo = 0. Zero economico-fiscale.

3. **Strategia naive non-LLM (EMA-cross deterministico)** — parametri vincolati per pre-registration:
   - **Segnale**: incrocio EMA(20) vs EMA(50) su candele 15m
   - **Regola di ingresso**: LONG quando EMA(20) crossover EMA(50) dal basso verso l'alto; SHORT quando crossover dall'alto verso il basso
   - **Simboli**: BTC, ETH, SOL trattati indipendentemente (3 sotto-strategie parallele, ognuna 333.33$ di equity allocata)
   - **Size per trade**: 20% dell'equity allocata al simbolo (allineato al guardrail dei modelli LLM)
   - **Leverage**: fisso 3× (valore intermedio del range concesso ai modelli)
   - **Stop loss**: 3% sotto/sopra entry price
   - **Take profit**: 6% sopra/sotto entry price (RR 2:1)
   - **Regola di uscita anticipata**: chiusura su EMA-cross inverso anche prima di SL/TP
   - **No overlap**: una sola posizione aperta per simbolo alla volta; nuovo segnale durante posizione aperta = ignorato
   - **Fee/funding/tax-sim**: applicati identicamente ai modelli LLM (parità di confronto)

   Razionale: la specificazione completa **prima** dell'esperimento previene l'ottimizzazione a posteriori del baseline. Parametri scelti a valori standard in letteratura quantitative trading (EMA 20/50 è il setup più comune in algorithmic trading retail), non tunati per overperformare.

**Per RQ2 (Spiegabilità dichiarata)**:
- `confidence_distribution` per modello (mean, std, percentili)
- `confidence_calibration_score` (Brier score: confidence dichiarata vs outcome netto positivo entro `time_horizon_min`)
- `reasoning_length` distribuzione (numero parole/caratteri)
- `key_signals_set` per decisione e per modello — **estratti da vocabolario controllato** (*), vedi sotto
- `coherence_score_internal` (*): misura di coerenza interna reasoning↔action↔confidence↔key_signals (definita operativamente in PRD V2 — non confondere con fedeltà causale, esplicitamente fuori scope)

**Vocabolario controllato dei `key_signals`** (*): il prompt impone al modello di scegliere i signal citati da una **lista fissa**, non da free-text. Lista preliminare (da finalizzare in PRD V2):
```
technical.rsi_extreme         technical.macd_cross        technical.ema_alignment
technical.bollinger_squeeze   technical.atr_spike         technical.support_resistance
sentiment.news_polarity       sentiment.fear_greed        sentiment.market_panic
onchain.funding_rate_extreme  onchain.open_interest_shift onchain.liquidation_cascade
market.volatility_regime      market.volume_anomaly       market.basis_perp_spot
portfolio.exposure_high       portfolio.unrealized_pnl    portfolio.position_aging
```

Razionale: senza vocabolario controllato la frequency analysis cross-model produrrebbe caos semantico (lo stesso concetto può essere espresso come "RSI extreme", "overbought", "momentum exhausted"). La lista controllata rende RQ2.4 e RQ3 statisticamente robusti.

**Per RQ3 (Profili comportamentali)**:
- `agreement_matrix_portfolio`: matrice 4×4 di Cohen's kappa cross-model sulle decisioni portfolio-level `(timestamp, model_id)`
- `fleiss_kappa_portfolio`: accordo a 4 giudici sulle decisioni portfolio-level (misura primaria, coerente con §1.0)
- `fleiss_kappa_action_symbol`: accordo a 4 giudici sulle azioni elementari `(timestamp, model_id, symbol)` (misura secondaria/derivata)
- `mean_leverage_requested` per modello
- `mean_size_pct_requested` per modello
- `mean_position_duration` per modello
- `n_open_positions_concurrent` per modello (distribuzione)
- `guardrail_activation_rate`: frequenza di `leverage_clamped`, `size_pct_clamped`, `forced_hold` per modello
- `regime_response_index`: comportamento del modello segmentato per quartile di volatilità realizzata BTC durante il periodo
- `behavioral_signature_vector` (*): vettore aggregato di tutte le metriche comportamentali sopra, per analisi di clustering tra modelli

---

## 4. Operazionalizzazione delle metriche

Mapping da concetto astratto a query SQL[^sql] eseguibile sul DB.

[^sql]: Una *query SQL* è un'interrogazione standard al database scritta in linguaggio SQL (es. `SELECT SUM(pnl) FROM decisions WHERE model_id = 'openai-gpt-5' AND created_at > NOW() - INTERVAL '7 days'`). Tradurre un concetto astratto come "fattibilità" in una query specifica significa renderlo *misurabile* e *verificabile* sui dati raccolti. Senza operazionalizzazione, il concetto resta filosoficamente discutibile ma scientificamente non testabile.

### 4.1 Fattibilità — operazionalizzazione

| Concetto | Operazionalizzazione |
|----------|---------------------|
| "PnL netto post-tax-sim positivo" | `sum(realized_pnl_usd) - sum(fee_usd) - sum(funding_usd) - tax_sim_26pct(realized_pnl_usd) > 0` per `model_id` |
| "Modello fattibile" | PnL netto post-tax-sim > 0 **con CI bootstrap a blocchi 95% interamente sopra 0** e confronto favorevole contro almeno un baseline non-LLM (§3.3), con significatività corretta via Holm-Bonferroni (§6.3). Il test t one-sample è riportato come riferimento descrittivo secondario, **non** come criterio inferenziale primario, per i motivi metodologici discussi in §6.2 (autocorrelazione, code pesanti, non-normalità) |
| "Cost-effectiveness" | rapporto `PnL_lordo / cost_api_cumulative` per modello |

### 4.2 Spiegabilità — operazionalizzazione

| Concetto | Operazionalizzazione |
|----------|---------------------|
| "Confidence calibrata" | Brier score: $\sum(confidence_i - outcome_i)^2 / N$, dove `outcome_i` = 1 se trade profittevole, 0 altrimenti |
| "Coerenza reasoning↔action" | Per ogni decisione: estrai parole chiave da `reasoning`, verifica match con `side`, `key_signals`. Score lessicale 0-1, da formalizzare in PRD |
| "Spiegabilità diversa cross-model" | ANOVA o Kruskal-Wallis su distribuzioni di reasoning_length, coherence_score, confidence_calibration tra i 4 modelli |

### 4.3 Profili comportamentali — operazionalizzazione

| Concetto | Operazionalizzazione |
|----------|---------------------|
| "Concordanza decisionale portfolio-level" | `fleiss_kappa_portfolio`: Fleiss' kappa su 4 raters sulle decisioni portfolio-level identificate da `(timestamp, model_id)`. Categoria: vettore aggregato delle azioni del portafoglio per quel tick (es. concordanza sul "pattern decisionale complessivo"). Unità di analisi: la decisione, coerentemente con §1.0. |
| "Concordanza decisionale action-level" | `fleiss_kappa_action_symbol`: Fleiss' kappa derivata sulle azioni elementari identificate da `(timestamp, model_id, symbol)`. Più granulare ma con minore indipendenza tra le 3 azioni di una stessa decisione. Riportata come **analisi secondaria**, non come misura primaria di accordo. |
| "Firma comportamentale" | Vettore di metriche per modello (mean_leverage, mean_size_pct, guardrail_rates, ecc.); distanza cosine o euclidea tra modelli; clustering k-means a 2 e 4 cluster come check |
| "Regime-dependent behavior" | Segmentazione del periodo per quartile di volatilità realizzata BTC; analisi separata per quartile; test di interazione modello × regime |

---

## 5. Aspettative pre-registrate

Cosa mi aspetto di trovare, e perché.

**Perché questa sezione esiste — il problema del p-hacking**

Con 4 modelli e ~6 metriche per RQ, esistono ~24 confronti statistici possibili. Per puro caso (anche se i modelli fossero perfettamente identici), 1-2 di questi confronti risulteranno "significativi" (p-value < 0.05). Se uno scienziato osserva tutti i confronti e poi racconta solo quelli significativi, sembra di avere trovato un effetto reale ma in realtà è rumore stocastico. Questa pratica è chiamata **p-hacking** (o *cherry-picking statistico*) ed è una delle cause principali della crisi di replicazione in molte scienze.

**La tutela**: pre-registrare le aspettative *prima* di vedere i dati. Se al termine dell'esperimento i risultati confermano le aspettative pre-registrate, le evidenze sono robuste. Se i risultati le smentiscono, sei costretto a dichiararlo esplicitamente come "scoperta inaspettata" e a discutere se sia un effetto reale o una possibile coincidenza statistica. Senza pre-registrazione, l'auto-inganno è quasi impossibile da evitare anche con buona fede.

Le aspettative seguenti sono quindi un atto di **trasparenza scientifica vincolante**: ciò che troviamo dovrà essere confrontato con esse, non scelto a posteriori per costruire una narrativa.

### Aspettative su RQ1 (Fattibilità)

- I 4 modelli **non genereranno tutti PnL netto post-tax positivo**. Il mercato crypto-perpetuals è efficiente al limite per agenti retail, e i costi (fee + funding + 26% tax) sono significativi. Aspetto 0-2 modelli con PnL netto leggermente positivo, 2-4 con PnL netto leggermente negativo.
- Almeno **un modello cheap costerà operativamente meno di un modello premium**, ma il gap PnL lordo potrebbe non compensare il risparmio: aspetto correlazione cost↔performance non lineare, possibile presenza di modello "sweet spot".
- **Latency** dovrebbe essere irrilevante per decisioni a 15m (anche 30 secondi di latency non perdono opportunità su orizzonte 15m), ma vale la pena misurarla.

### Aspettative su RQ2 (Spiegabilità dichiarata)

- I modelli premium con reasoning trace esposto (es. R1, Sonnet thinking mode) **avranno reasoning_length sistematicamente maggiore**. Non è notizia, ma va misurato per contesto.
- La **calibrazione di confidence è probabilmente cattiva** in tutti i modelli: gli LLM sono noti per essere over-confident. Aspetto Brier scores > 0.20 (calibrazione perfetta = 0).
- I `key_signals` citati **potrebbero divergere tra famiglie di modelli** — *aspettativa esplorativa non inferenziale*, basata su ipotesi narrative sui training data delle aziende (coerente con RQ3.5). Sarà valutata descrittivamente; il confronto USA vs CN è suggestivo, non test statistico conclusivo (vedi §7 limitazione 7).

### Aspettative su RQ3 (Profili comportamentali)

- **Forte aspettativa di profili comportamentali distinti** (model-associated, non model-attribuibili in senso causale). Fleiss' kappa cross-modelli probabilmente sarà 0.2-0.5 (concordanza moderata, non casuale ma nemmeno alta). Sarebbe un risultato sorprendente trovare kappa > 0.7 (intercambiabilità funzionale) o kappa < 0.1 (decisioni completamente indipendenti).
- **Stili di trading diversi**: aspetto modelli più "aggressivi" (alta leva, alte size) e più "conservativi" (bassa leva, frequente HOLD). Possibile correlazione con tier (premium = più audaci? o più cauti?) — questo è un punto da osservare attentamente.
- **Cluster k-means a 2** — *analisi esplorativa non inferenziale, non test statistico*: potrebbe trovare separazione USA/CN OPPURE premium/cheap, ma non entrambi simultaneamente con N=4. Sarebbe interessante quale dei due assi prevale, da trattare come *case study descrittivo* di 4 specifici modelli (coerente con §7 limitazione 7).

---

## 6. Design statistico

### 6.1 Potenza statistica

Stima conservativa di osservazioni utili per cella (unità: decisione portfolio-level, vedi §1.0):
- 4 settimane × 7 giorni × 24 ore × 4 (cron 15m) = **2.688 cicli decisionali per modello**
- Numero decisioni *attive* (non interamente HOLD): aspetto 30-50% di HOLD → 1300-1900 decisioni attive per modello
- Numero trade chiusi (con outcome osservabile): 800-1500 per modello

Sufficiente per:
- Test su differenze cross-model con effect size medio (d ≈ 0.3): power > 0.80 con α = 0.05
- Bootstrap a blocchi CI 95% su PnL cumulativo
- Fleiss' kappa stabile

Insufficiente per:
- Sub-analisi su singoli ticker × regime di mercato (potenza ridotta del 50-70%)
- Regression con > 5 covariate
- Test inferenziali a livello geografico USA/CN (4 modelli totali, 2 per cella, non sufficienti)

### 6.2 Non-indipendenza dei dati e bootstrap a blocchi (modifica metodologica chiave)

**Problema riconosciuto**: il PnL prodotto da decisioni a 15 minuti **non è una variabile i.i.d. normale**. Tre fonti di non-indipendenza:
1. **Autocorrelazione di mercato**: i prezzi crypto mostrano volatilità clusterizzata e momentum (correlazione seriale dei rendimenti)
2. **Correlazione decisionale intra-modello**: se un modello apre una posizione e la mantiene per 10 tick, i 10 PnL contigui sono fortemente correlati (la stessa posizione)
3. **Distribuzioni non normali**: i rendimenti crypto hanno code pesanti, kurtosi alta, asimmetrie significative

**Conseguenza**: il test t one-sample classico sul PnL medio è **metodologicamente debole** in questo contesto. Lo riportiamo solo come riferimento descrittivo secondario.

**Metodo inferenziale principale (per RQ1 e tutte le metriche PnL-derivate)**: **bootstrap a blocchi temporali** (block bootstrap), che preserva la struttura di correlazione temporale.
- Block length: scelto da preliminare analisi di autocorrelazione dei PnL (tipicamente 6-24 blocchi orari)
- Resampling: 1.000-10.000 iterazioni per CI al 95%
- Output: distribuzione bootstrap della media PnL cumulativo per modello, confrontata cross-model e contro i 3 baseline (§3.3)

**Metodi secondari**:
- Test t e Mann-Whitney U riportati solo per completezza descrittiva, con esplicito caveat sulla loro debolezza in presenza di autocorrelazione
- Per metriche aggregate non temporali (es. distribuzione di confidence) il test t/Mann-Whitney mantiene validità

### 6.3 Multiple comparisons

3 RQ × multiple sub-questioni per RQ → ~12-15 test totali. Applicazione di Bonferroni o Holm-Bonferroni[^bonf] per il significance testing. Nei casi dove l'analisi è descrittiva (clustering, distribuzioni), riporto effect size senza p-value formale.

[^bonf]: *Correzione di Bonferroni* è il metodo più semplice per gestire confronti multipli: dividi la soglia di significatività α per il numero di test. Esempio: 15 test con α = 0.05 → soglia corretta per ogni test = 0.05/15 ≈ 0.0033. Ogni p-value individuale deve passare questa soglia più severa per essere considerato significativo. *Holm-Bonferroni* è una variante più potente che ordina i p-value e applica soglie progressive (più rigida sui primi, meno sui successivi), mantenendo la stessa garanzia di errore complessivo. È lo standard contemporaneo per analisi con confronti multipli.

### 6.4 Robustness checks

- **Bootstrap PnL distribution** (1000-10000 resample, block-bootstrap come da §6.2) per ogni modello e baseline, intervalli di confidenza al 95%
- **Sensitivity analysis sui guardrail**: rimuovere virtualmente ciascun guardrail e ricalcolare PnL ipotetico (cosa sarebbe successo senza il vincolo). Analisi controfattuale.
- **Out-of-sample check**: split delle 4 settimane in 2+2 e confronto stabilità delle metriche tra le due metà
- **Logging di tutte le variabili di nuisance** (*): per ogni decisione persistiamo `model_version`, `provider`, `temperature`, `top_p`, `prompt_hash`, `context_hash`, `schema_version`, `retry_count`, `fallback_used`, `latency_ms`, `api_cost_usd`. Questo permette analisi a posteriori di stabilità del risultato rispetto a variazioni minori di configurazione.

---

## 7. Limitazioni dichiarate del design

Onestà scientifica: cosa NON posso concludere da questi dati.

1. **Non posso generalizzare a mainnet con capitali significativi.** Testnet, capitale fisso 1000$, dimensione delle posizioni piccole. Effetti come slippage e market impact non emergono.
2. **Non posso generalizzare a frequenze diverse.** Cron 15m è una scelta arbitraria. Risultati a 5m o 1h potrebbero essere qualitativamente diversi.
3. **Non posso generalizzare ad altri ticker.** BTC/ETH/SOL hanno alta liquidità e bassa volatilità rispetto ad altcoin minori. Mercato selezionato.
4. **Non posso isolare l'effetto del singolo cambio di contesto.** Il prompt è uno solo. L'ablation study (variare news/sentiment/forecasts) è esplicitamente fuori scope (vedi PRE_PRD §1.5).
5. **Non posso inferire causalità su "perché" un modello si comporta in un certo modo.** I dati mostrano *che* succede, non *perché*. Ipotesi causali (training distribution, RLHF, scala) restano speculative — vanno esplicitate come tali in tesi. Per questo motivo le RQ usano "model-associated", non "model-attribuibile".
6. **Post-hoc rationalization gap** (limitazione epistemologica chiave). Il campo `reasoning` prodotto dai modelli è una **self-explanation linguistica**, non un accesso trasparente al processo computazionale interno. La letteratura sull'interpretability degli LLM (Anthropic et al.) ha mostrato che giustificazioni narrative possono divergere dai meccanismi causali reali. Per questo RQ2 misura *coerenza interna* (livello 2.A), non *fedeltà causale* (livello 2.B). Affermazioni su "il modello capisce davvero" o "il modello inganna" sono fuori dalla portata di questo design.
7. **4 modelli sono pochi per inferire pattern stabili "USA vs CN" o "premium vs cheap".** Il design 2×2 è suggestivo ma non statisticamente conclusivo a livello di geografia o tier. RQ3.4 è analisi esplorativa secondaria, non test inferenziale conclusivo.
8. **Memoria 2 disabilitata** è una scelta. I risultati riflettono il comportamento dei modelli senza apprendimento dalla propria storia: è una semplificazione rispetto a un eventuale uso "in produzione". Vedi PRE_PRD §11.3 e §8.3.
9. **Periodo di 4 settimane** è breve rispetto alla volatilità strutturale di crypto. Una bull/bear di mercato durante l'esperimento può confondere i risultati. Va riportato il regime di mercato del periodo e discusso.
10. **Una sola configurazione per modello** (temperature, top_p, prompt unico). Non possiamo separare l'effetto "modello" dall'effetto "configurazione specifica del modello". Lo riconosciamo esplicitamente: tutte le affermazioni cross-model si intendono "sotto la configurazione adottata".
11. **Confidence calibration limitata dall'orizzonte temporale** dichiarato dal modello. Se un modello dichiara confidence + `time_horizon_min = 240`, dobbiamo aspettare 4 ore per osservare l'outcome reale. Posizioni che restano aperte oltre il `time_horizon` o che chiudono prima per SL/TP introducono complicazioni di attribuzione che vanno gestite in fase di analisi.

---

## 8. Output scientifico atteso

### 8.1 Capitoli della tesi — struttura indicativa, NON definitiva

> Quanto segue è una **proposta minimale di riferimento** sull'architettura della tesi, sufficiente a coprire i risultati prodotti da questo Research Design. La tesi finale sarà più ricca e personale: la struttura definitiva verrà definita in fase di scrittura, lasciando libertà all'autore di aggiungere capitoli che approfondiscano la storia personale del progetto, il contesto filosofico-disciplinare, la letteratura, e la discussione interdisciplinare.

**Capitoli minimi (output diretto del lavoro empirico)**:

1. **Introduzione e motivazione**: AI/LLM come decisori autonomi, gap nella letteratura, le 3 dimensioni di indagine
2. **Research Design** (basato su questo documento)
3. **Architettura del sistema** (basato sul PRD V2)
4. **Risultati**: 3 capitoli, uno per RQ
5. **Limitazioni e future work**
6. **Conclusioni**

**Capitoli espansivi tipici di una tesi in Filosofia e IA, da pianificare in fase di scrittura**:

- **Storia dell'idea e contesto personale**: come l'autore è arrivato a questa ricerca, l'eredità del periodo di test V1, l'ispirazione tratta da iniziative come Nof1 e altri esperimenti pubblici di LLM-as-trader
- **Stato dell'arte esteso**: TradingAgents (Xiao et al., 2025), letteratura su LLM agents, calibrazione delle confidence, interpretability research di Anthropic e altri laboratori, paper su Holistic Evaluation of Language Models (HELM)
- **Inquadramento filosofico**: epistemologia degli LLM come decisori, problemi di interpretabilità, agency artificiale, opacità decisionale, accountability
- **Discussione interdisciplinare dei risultati**: cosa significano i comportamenti emergenti per la teoria della cognizione artificiale, per le politiche di regolamentazione AI, per il concetto di "decisione informata"
- **Conclusioni etico-politiche**: implicazioni dell'uso di LLM come decisori finanziari autonomi, considerazioni di governance

La struttura finale della tesi è prerogativa dell'autore. Questo documento si limita a garantire che il lavoro empirico produca tutti i contenuti necessari a sostenere il capitolo dei Risultati e a fornire materiale per la discussione.

### 8.2 Deliverable scientifici

- **Dataset**: ~10.752 decisioni portfolio-level + ~32.256 azioni elementari (decisione × simbolo) + ~3.000-6.000 trade chiusi con outcome, esportato come CSV/Parquet, condivisibile (eventualmente anonimizzato per chiavi)
- **Codice**: repo agent V2 + dashboard V2, riproducibili (versione git pinned, container Docker disponibili)
- **Tesi scritta**: ~50-80 pagine (struttura indicativa in §8.1, definitiva a discrezione dell'autore)
- **Pre-registrazione**: questo Research Design committato in git prima dell'inizio dell'esperimento è esso stesso un atto di trasparenza scientifica vincolante (vedi §5)

### 8.3 Aspettative sul futuro post-tesi

- **Future work prioritari**:
  - (a) **Ablation study su composizione contesto** — variare le 4 sezioni del prompt (Technical, Sentiment, News, On-chain) per misurare il contributo di ciascuna alle decisioni. Il `ContextBuilder` v2 è già progettato modulare per supportarlo.
  - (b) **Attivazione di Memoria 2 come pattern di in-context learning** — ovvero adottare esplicitamente il pattern di *in-context learning from own history* introdotto da TradingAgents (Xiao et al., 2025) nel `TradingMemoryLog`. La nostra tesi triennale isola la variabile modello escludendo questo meccanismo (vedi §3.2); una futura estensione può attivare il flag `inject_decision_history=True` e confrontare sistematicamente le firme comportamentali con e senza in-context learning. Sarebbe il primo studio che disambigua "comportamento del modello puro" da "comportamento del modello + memoria della propria storia".
  - (c) **Sperimentazione su più simboli e timeframe** — estensione a altcoin minori, timeframe più brevi (5m) o più lunghi (1h, 4h).
  - (d) **Confronto con strategie quantitative classiche** — momentum, mean reversion, arbitraggio statistico come benchmark "non-LLM".

- **Possibile estensione magistrale o paper accademico**: la combinazione di (b) e (a), realizzata sull'infrastruttura già esistente, costituirebbe un design fattoriale 2 (memoria sì/no) × 4 (modelli) × N (varianti contesto). Rilevante per la letteratura su agentic LLM in dominio finanziario.

---

## 9. Conformità con il PRE_PRD

Verifica esplicita che il Research Design è *coerente* con le 18 decisioni del PRE_PRD. In caso di conflitto, prevale il Research Design (essendo più specifico) e il PRE_PRD va aggiornato.

| Decisione PRE_PRD | Riflesso nel Research Design |
|-------------------|------------------------------|
| §1.1 — 4 modelli design 2×2 | §3.1 (variabile indipendente principale) + §1 RQ3.4 (analisi esplorativa secondaria, non test inferenziale) |
| §1.2 — 4 servizi Railway separati | §3.2 (isolation) + §3.1 (wallet distinti) |
| §1.4 — Esperimento comparativo singola condizione | §1, §2, §6 (design intero) |
| §1.5 — Setup ottimizzato baseline | Implicito in §3.2 (variabili costanti) |
| §1.6 — ContextBuilder modulare | §7 limitazione #4 + §8.3 future work |
| §11.3 — Memoria 1 sì, Memoria 2 off | §3.2 (memoria 2 = variabile costante OFF) + §7 limitazione #8 |
| §11.4 — 4 sezioni del prompt | §3.2 (composizione contesto fissa) |
| §11.5 — Quadrupletta PnL + benchmark spot | §3.3 (variabili dipendenti RQ1) + 3 baseline espliciti |
| §11.6 — Prompt versioning SHA | §3.2 (prompt unico, hash registrato) + §6.4 (logging completo) |
| §11.7 — Cost ledger | §3.3 (cost_usd_cumulative + cost_api_to_pnl_ratio variabili dipendenti) |
| §11.8 — Test isolamento cross-model | §6.4 (robustness) |
| §11.9 — TradeDecision schema | §3.3 (operazionalizzazione metriche) + §2.1 (definizione confidence vincolata) |
| §13.3 — 4 guardrail risk management | §3.2 (variabili costanti) + §3.3 (guardrail_activation_rate) |

**Nuove specifiche di Research Design da propagare al PRD V2** (decisioni nate qui, da formalizzare nel PRD tecnico):
- §1.0 unità di osservazione = decisione portfolio-level → schema `decisions` + `decision_actions` (1-to-many)
- §2.1 definizione confidence vincolata nel prompt
- §3.3 vocabolario controllato `key_signals` (lista preliminare)
- §3.3 baseline espliciti: Buy-and-Hold, Cash, momentum naive
- §3.3 metriche rischio finanziario standard (Sharpe, Sortino, drawdown, ecc.)
- §6.2 bootstrap a blocchi temporali come metodo statistico principale
- §6.4 logging esteso di variabili di nuisance (provider, temperature, top_p, hash, ecc.)

Conformità verificata. Nessun conflitto. Le specifiche nuove vanno propagate al PRD V2.

---

*Fine RESEARCH_DESIGN v3 (post 2 cicli di peer-review esterna, 10 raccomandazioni v1→v2 + 4 micro-fix v2→v3 integrati). Pronto per pre-registrazione scientifica via git commit. Prossimo documento: `PRD_V2.md` (architettura tecnico-implementativa).*