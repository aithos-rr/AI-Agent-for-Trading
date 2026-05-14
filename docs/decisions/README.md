# Architecture Decision Records (ADR)

Questa directory contiene gli ADR del progetto AI Trading Agent V2.

## Cosa è un ADR

Un Architecture Decision Record documenta una decisione architetturale o
implementativa che si discosta dal `PRD_V2.md` (frozen) o lo estende/raffina
sulla base di evidenze emerse durante l'implementazione.

Gli ADR sono **immutabili** una volta in stato `accepted`. Se una decisione
viene sostituita, si crea un nuovo ADR con `Status: supersedes ADR-XXXX`,
e l'originale viene marcato `superseded by ADR-YYYY`.

## Quando creare un ADR

- Deviazione dal PRD V2 (cambia comportamento o struttura rispetto a quanto
  documentato)
- Chiusura di una *bounded deferral* di PRD §15.4 (D1-D5)
- Decisione implementativa non coperta dal PRD che ha implicazioni durature
  (non per micro-scelte locali di una funzione)

## Quando NON creare un ADR

- Implementazione che segue fedelmente il PRD V2 (è già documentato lì)
- Refactoring interno senza cambio di API esposta
- Bug fix puntuali con test che riproduce il bug

## Convenzione naming

`NNNN-titolo-breve-kebab-case.md` dove NNNN è il numero progressivo a 4 cifre.

## Indice degli ADR accettati

| ID | Titolo | Status | Data | Milestone | Closes deferral |
|----|--------|--------|------|-----------|-----------------|
| 0001 | Adozione del pattern ADR per Phase 5 | accepted | 2026-05-14 | M0 | none |

## Template

Vedi `0000-template.md` per il template ADR standard.
