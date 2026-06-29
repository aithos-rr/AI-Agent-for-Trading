# ADR-0020: Struttura dei 4 modelli LLM (D1-struttura; nomi commerciali al seed)

**Data**: 2026-06-29
**Status**: accepted
**Milestone**: M5/M6 (struttura), nomi commerciali congelati a **M7 step 4** (seed_experiment.py)
**PRD reference**: §15.4 **D1** (riga 3614); RESEARCH_DESIGN §7 (limitazioni dichiarate)
**Closes deferral**: D1 **parzialmente** — fissa la STRUTTURA (provider/geography/tier/criterio
+ `id` stabili); i `model_name_api` commerciali restano deferiti al seed M7 (D1 PRD riga 3614).

## Contesto

D1 (PRD §15.4, riga 3614) congela i `model_name_api` **esatti** al seed di M6/M7 ("voglio i
modelli più recenti al momento del seed, immutabili da lì"). Quindi **oggi** fissiamo solo la
**struttura** dei 4 slot — provider, geografia, tier e criterio di classificazione — **non** i
nomi commerciali, che si scelgono al seed coi modelli correnti dei provider.

Questo serve a: (a) stabilizzare gli `id` dei modelli (che finiscono in FK denormalizzate e
nei filtri inv #1 `WHERE model_id = $AIAT_MODEL_ID`) **prima** dell'implementazione del seed;
(b) rendere espliciti — e pre-registrati — i limiti di validità del disegno comparativo.

## Decisione

### Matrice 2×2 — 4 modelli (ogni cella piena, ogni provider una volta)

| `id` (stabile) | provider  | geography | tier        | `model_name_api` (PROVVISORIO, riferimento) | list price in/out |
|----------------|-----------|-----------|-------------|---------------------------------------------|-------------------|
| `usa-premium`  | anthropic | USA       | `premium`   | (rif. Opus 4.8)                             | 5.00 / 25.00      |
| `usa-cheap`    | openai    | USA       | `cheap_alt` | (rif. GPT-5.4 mini)                         | 0.75 / 6.00       |
| `cn-premium`   | qwen      | CN        | `premium`   | (rif. Qwen3.7-Max, list price)              | 2.50 / 7.50       |
| `cn-cheap`     | deepseek  | CN        | `cheap_alt` | (rif. DeepSeek V4 Pro)                       | 1.74 / 3.48       |

Gli `id` sono **geography-tier**, stabili: NON cambiano quando i `model_name_api` verranno
congelati al seed. Ogni provider (openai/anthropic/deepseek/qwen) compare **una volta**.

### Criterio di classificazione tier = COSTO ASSOLUTO DI MERCATO

Il `tier` è assegnato in base al **costo assoluto di mercato (list price, non promozioni
temporanee)**, **NON** alla capacità del modello né alla sua posizione flagship/economico
*interna* al provider.

Ordinamento per costo input: Opus 5.00 > Qwen 2.50 > DeepSeek 1.74 > GPT-mini 0.75.
- I due **`premium`** = i due più cari in assoluto: **Opus (USA)** e **Qwen (CN)**.
- I due **`cheap_alt`** = i due meno cari in assoluto: **DeepSeek (CN)** e **GPT-mini (USA)**.

### Parametri deterministici (tutti i modelli, riproducibilità)

`temperature = 0`, `seed = 42` per tutti e 4 i modelli (output il più deterministico possibile;
gli scostamenti residui sono attribuibili al modello, non al sampling).

## Limiti / threats to validity (dichiarati, coerente con RESEARCH §7)

Da riportare esplicitamente nella discussione dei risultati (stesso pattern di RESEARCH §7,
es. limitazione #7 "confronto USA vs CN suggestivo, non test statistico conclusivo"):

1. **Tier = costo assoluto con soglie NON simmetriche tra geografie.** Un `premium` CN (Qwen
   2.50) costa **meno** di un `premium` USA (Opus 5.00). Il `tier` **non** è una soglia di
   prezzo assoluta uniforme né una misura di capacità: è un'etichetta ordinale entro l'insieme
   dei 4 modelli scelti (i 2 più cari vs i 2 meno cari).
2. **Confound parziale tier ↔ geography ↔ provider.** Con 4 provider distinti su 4 slot, gli
   effetti di provider, geografia e tier **non sono pienamente separabili**. Il disegno resta
   valido come **studio comparativo descrittivo** di 4 modelli rappresentativi (case study,
   coerente con RESEARCH §7 / §6.1), **NON** come esperimento fattoriale che isola un singolo
   fattore. Nessuna inferenza causale su "il tier/la geografia causa X".
3. **`model_name_api` PROVVISORI.** I nomi commerciali in tabella sono riferimenti; si
   **congelano al seed M6/M7** (`scripts/seed_experiment.py`, D1 PRD riga 3614) coi modelli
   correnti dei provider. I list price in tabella sono verificati a **giugno 2026** e vanno
   **ri-verificati al seed** (il `cost_event.pricing_snapshot` registra comunque il pricing
   usato per ogni invocazione).

## Conseguenze

### Positive
- `id` dei 4 modelli stabili e tracciabili (geography-tier) prima del seed → niente churn su
  FK/filtri inv #1 quando si fissano i nomi commerciali.
- Criterio tier esplicito e riproducibile (list price), non un giudizio soggettivo di capacità.
- Determinismo (temp=0, seed=42) pre-registrato per tutti i modelli.

### Negative / Note
- Confound strutturale (limite #2) intrinseco a N=4 con un provider per slot — accettato e
  dichiarato; il disegno è descrittivo-comparativo per costruzione (RESEARCH §0/§7).
- `tier` non confrontabile come soglia assoluta cross-geografia (limite #1).

## Propagazione

- [x] `docs/decisions/0020-model-structure.md` (questo ADR)
- [x] `src/aiat/config/model_pricing.yaml`: 4 chiavi = `id` D1 + list price (reasoning provv.)
- [x] `docs/decisions/README.md` aggiornato
- [ ] `scripts/seed_experiment.py` (task separato): registra i 4 `id` + `model_name_api`
      correnti al seed (chiude D1 completamente, M7 step 4)
- [ ] Limiti #1/#2 da riportare nel capitolo Discussione della tesi (RESEARCH §7)
