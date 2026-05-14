# ADR-0001: Adozione del pattern ADR per Phase 5

**Data**: 2026-05-14
**Status**: accepted
**Milestone**: M0
**PRD reference**: §15 (Handoff Phase 5)
**Closes deferral**: none

## Contesto

Il `PRD_V2.md` è stato congelato (tag `prd-v2-frozen`, commit `22d3119`)
dopo 6 cicli di peer-review esterna e 48 fix integrati. È il blueprint
tecnico definitivo per l'implementazione.

Tuttavia, durante l'implementazione (M0-M7) è prevedibile che emergano
deviazioni dal PRD per tre ragioni:

1. **Bounded deferrals** esplicitamente dichiarate in PRD §15.4
   (D1-D5: modelli LLM, HOLD/FLAT labeling, exception class, controlled
   signals, RSS sources) che richiedono decisioni puntuali entro
   specifiche milestone.

2. **Evidenze tecniche** emerse dallo sviluppo (es. comportamento reale
   dei 4 provider LLM, performance dei collectors, comportamento HL
   testnet) che possono richiedere raffinamenti del design.

3. **Errori residui** del PRD V2 che potrebbero essere scoperti durante
   l'implementazione concreta (nessun documento è perfetto, e 6 cicli di
   review non eliminano tutti i casi limite).

In tutti e tre i casi, modificare il PRD V2 direttamente sarebbe dannoso:
si perderebbe la tracciabilità della decisione, e si rischierebbe di
sporcare il blueprint con dettagli implementativi che appartengono al
livello del codice.

## Decisione

Adottiamo il pattern **Architecture Decision Record (ADR)** per documentare
ogni deviazione, chiusura di deferral, o decisione implementativa con
implicazioni durature.

Gli ADR vivono in `docs/decisions/`, sono in formato Markdown, sono
**immutabili una volta accettati**, e seguono il template di
`0000-template.md`.

Il `PRD_V2.md` resta la fonte di verità *originaria*; gli ADR sono la
fonte di verità *evolutiva*. Quando una sezione del PRD viene modificata
da un ADR, il PRD stesso può essere annotato con un commento che cita
l'ADR (es. *"vedi ADR-0007 per la decisione finale"*), ma il testo
originale resta in storia git.

## Conseguenze

### Positive
- Tracciabilità totale delle decisioni: dal commit di codice all'ADR alla
  sezione PRD originale
- Cita-bile in tesi: capitolo "Metodologia di sviluppo" può fare
  riferimento esplicito agli ADR
- Onboarding futuro: se in futuro estendessi il progetto, gli ADR
  spiegano il *perché* delle scelte
- Riduce la tentazione di modificare il PRD frozen

### Negative
- Overhead: ogni decisione non-banale richiede un file Markdown formale
  (mitigabile: per micro-decisioni si usa commento al PR, non ADR)

### Neutre
- Necessità di disciplina nel ricordarsi di creare l'ADR (mitigabile
  con CLAUDE.md che lo richiama nel workflow)

## Alternative considerate

### Alternativa A: modificare direttamente il PRD V2 quando serve
- Pro: nessun overhead di file extra
- Contro: si perde il significato di "PRD frozen", la tracciabilità è
  affidata al git log che non è ottimizzato per decisioni discrete
- Scartata perché: il valore del freeze sta proprio nell'immutabilità
  del documento come pre-registrazione scientifica

### Alternativa B: commenti TODO/NOTE nel codice
- Pro: zero overhead, vive insieme al codice
- Contro: granularità sbagliata (codice cambia spesso, decisioni
  architetturali no), non cita-bile in tesi
- Scartata perché: serve un livello dedicato per le decisioni con
  implicazioni durature

## Test gating

N/A — pattern documentale, non implementativo. Disciplina manuale.

## Propagazione

- [x] Creata directory `docs/decisions/`
- [x] Creato `docs/decisions/0000-template.md`
- [x] Creato `docs/decisions/README.md` con indice
- [x] Questo ADR (0001) creato come primo entry
- [ ] CLAUDE.md (in M0 successivo) richiamerà la regola di creare ADR
      quando si chiude una deferral o si devia dal PRD
