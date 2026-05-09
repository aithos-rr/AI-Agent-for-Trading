# Analisi Figma come Reference per V2

> Il Figma del progetto era una *mind map architetturale stile n8n* (nodi + annotazioni), non un mockup UI. Era una bozza utopistica iniziale, senza considerazione dei vincoli tecnici. Questo documento NON è una review di design UI, è l'estrazione dei **3 requisiti operativi** che l'autore vuole assolutamente preservati nella V2 finale, formalizzati come specifiche eseguibili con razionale scientifico.

> Branch: `prd/v2-design`. Output: 3 requisiti formalizzati (F1/F2/F3) e 1 strategia integrata di risk management (C+ ibrida) che li compone.

---

## 0. Sintesi esecutiva

Il Figma originale serve a una sola cosa: **conferma che 3 requisiti dichiarati dall'autore in fase di brainstorming devono entrare nel PRD V2**. Tutti e 3 erano già parzialmente presenti nelle decisioni del PRE_PRD §1 e §11, ma erano sotto-specificati. Questa fase li rende eseguibili e li integra in un'unica strategia di risk management coerente (Strategia C+).

---

## 1. Requisito F1 — Stop Loss e Take Profit obbligatori per ogni apertura

**Dichiarazione utente**: *"stop loss e take profit per ogni operazione"*.

**Stato pre-Fase 3**: PRE_PRD §11.9 aveva `stop_loss_pct` e `take_profit_pct` come `Optional[float]`. Insufficiente.

**Formalizzazione**: lo schema `TradeDecision` viene reso **conditionally required**:
- Quando `side ∈ {LONG, SHORT}` (apertura nuova posizione o inversione): `stop_loss_pct` e `take_profit_pct` sono **obbligatori**, validati come `Required[float]`
- Quando `side ∈ {FLAT, HOLD}`: i due campi sono `null` (validato esplicitamente)

A livello di Pydantic implementabile con `model_validator` post-validation oppure con due schemi distinti (`OpenDecision` vs `CloseOrHoldDecision`) e `Union` discriminato su `side`.

**Razionale scientifico**: senza SL, il rischio di liquidation rapida è reale anche su testnet 1000$. Per la tesi un modello che esaurisce il capitale in pochi giorni non genera dati su "fattibilità", genera dati di "fallimento rapido". Lo SL obbligatorio garantisce continuità sperimentale e rende confrontabili i modelli su 4 settimane complete.

## 2. Requisito F2 — Confidence dichiarata per ogni decisione

**Dichiarazione utente**: *"confidence per ogni operazione"*.

**Stato pre-Fase 3**: PRE_PRD §11.9 aveva già `confidence: float = Field(ge=0.0, le=1.0)`. Coperto.

**Estensione di Fase 3**: la confidence è obbligatoria **anche per `Side.HOLD` e `Side.FLAT`**, non solo per aperture/chiusure. Razionale: la confidence con cui un modello *decide di non operare* è un dato scientifico utile per la dimensione spiegabilità (un modello che fa HOLD con confidence 0.95 è epistemicamente diverso da uno che fa HOLD con confidence 0.3).

**Implicazione DB**: `decisions.confidence` è `NOT NULL` sempre, indipendentemente dal `side`.

## 3. Requisito F3 — Leva selezionabile per ticker, con cap di rischio

**Dichiarazione utente**: *"possibilità di selezionare leva anche massima per ogni ticker ma deve essere comunque settato su un rischio medio, non deve fare il folle se non è sicuro, senno brucia capitale prima di 4 settimane"*.

**Formalizzazione come Strategia C+ (ibrida)**: 4 guardrail operativi compongono il risk management complessivo del sistema.

### 3.1 SL obbligatorio (vedi F1) ← protezione primaria
Già definito in §1 sopra.

### 3.2 Cap esposizione per trade
`size_pct_equity ≤ AIAT_MAX_SIZE_PCT` (env-var, default `0.20`).

Significato: ogni singola posizione non può eccedere il 20% dell'equity. Limite duro applicato lato execution layer (clamping del valore richiesto dal modello prima dell'invio a Hyperliquid). Garantisce massimo 5 posizioni concorrenti sul capitale totale.

### 3.3 Cap leva = funzione lineare della confidence
`max_leverage_allowed = 1 + confidence × 9`, con hard ceiling a `AIAT_HARD_MAX_LEVERAGE` (env-var, default `10` = limite Hyperliquid testnet).

Esempi:
- `confidence = 0.4` → `max_leverage = 4.6x`
- `confidence = 0.6` → `max_leverage = 6.4x`  
- `confidence = 0.9` → `max_leverage = 9.1x`

Significato: il modello *può* dichiarare alta leva solo se è anche molto sicuro. La formula lega direttamente due variabili dichiarate dal modello (leverage e confidence), creando una correlazione **forzata** tra rischio richiesto e fiducia dichiarata. Implementato lato validation: se `decision.leverage > 1 + decision.confidence × 9`, il valore viene clampato al massimo permesso (logged come `leverage_clamped: true` per analisi successiva).

### 3.4 Confidence threshold per aperture
Se `confidence < AIAT_MIN_OPEN_CONFIDENCE` (env-var, default `0.4`) e `side ∈ {LONG, SHORT}`, l'execution layer **forza** `side = HOLD` (decisione sostituita, original loggato come `forced_hold: true`).

Significato: sotto la soglia di confidence, il modello non opera. Guardrail post-decisione, non vincolo nel prompt (il modello è libero di chiedere apertura con bassa confidence; il sistema poi non esegue).

### 3.5 Riepilogo dei 4 guardrail in tesi

> *"Al modello sono stati imposti quattro guardrail di prudenza operativa: (1) stop loss obbligatorio per ogni apertura, (2) esposizione massima per trade del 20% dell'equity, (3) leva massima funzione lineare della confidence dichiarata (formula `1 + c·9`, hard cap a 10x), (4) soglia minima di confidence 0.4 per aperture. Tutti gli altri parametri decisionali (direzione, size esatto entro il cap, durata, prezzo limit, ecc.) sono lasciati alla discrezione del modello."*

## 4. Implicazioni schema DB

Le tabelle che il PRD V2 dovrà definire devono prevedere campi aggiuntivi per tracciare l'attivazione dei guardrail:

- `decisions.leverage_requested` (quanto chiedeva il modello)
- `decisions.leverage_executed` (cosa è stato effettivamente eseguito post-clamping)
- `decisions.leverage_clamped` (boolean: il guardrail 3.3 si è attivato?)
- `decisions.size_pct_clamped` (boolean: il guardrail 3.2 si è attivato?)
- `decisions.forced_hold` (boolean: il guardrail 3.4 si è attivato?)
- `decisions.confidence` (sempre presente, anche per HOLD/FLAT)

Questi campi servono per **analisi a posteriori** dei guardrail: *"quante volte ciascun modello ha chiesto leva oltre il consentito? Quanto spesso ha chiesto aperture sotto-soglia? È un comportamento sistematico o sporadico?"* — domande direttamente collegate alla dimensione spiegabilità della tesi.

## 5. Cose del Figma originale NON formalizzate

Il Figma conteneva idee utopistiche scartate consapevolmente:
- Architettura n8n-style con nodi (sostituita da pipeline lineare Python, vedi PRE_PRD §11.4)
- Feature visuali UI (saranno affrontate in Fase 6 con Claude design, non sono scope tesi)
- Dettagli tecnici non specificati (sostituiti da decisioni rigorose in PRE_PRD §11)

Il Figma è quindi archiviato come *historical artifact*, non come specifica di design.

## 6. Verdetto finale

Fase 3 ha prodotto **3 requisiti formalizzati** (F1/F2/F3) e **1 strategia integrata di risk management** (C+ con 4 guardrail). Il PRE_PRD verrà esteso con un §13 dedicato che ne incorpora il contenuto. Niente altro emerge dal Figma che non sia già nel PRE_PRD aggiornato a §11.

Fase 3 ✅ DONE. Prossimo passo: PRE_PRD update §13, poi Fase 4 (Research Design + PRD V2).

---

*Fine ANALYSIS_FIGMA.md.*