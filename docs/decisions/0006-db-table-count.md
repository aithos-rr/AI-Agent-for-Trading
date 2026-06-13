# ADR-0006: Conteggio tabelle DB — 20, non 17

**Data**: 2026-06-13
**Status**: accepted
**Milestone**: M1
**PRD reference**: §3.1, §3.2, §12 (M1 DoD)
**Closes deferral**: none (correzione di incoerenza interna al PRD)

## Contesto

Il `PRD_V2.md` contiene un'incoerenza interna sul numero di tabelle/modelli del
database, emersa durante la generazione di `TASKS.md` (Fase 1 → Fase 2):

- §3.1 (Mappa entità) e §12 (M1 Definition of Done) dichiarano **"17 SQLAlchemy
  models"** / "17 tabelle".
- Il DDL completo §3.2 contiene invece **20 `CREATE TABLE`**.

Il conteggio testuale "17" è il residuo di una versione precedente del DDL: durante
i 3 round del PRD (e i fix di peer-review B.2/B.5) sono state aggiunte tabelle
(`context_build_runs`, `baseline_configs`, `baseline_equity_snapshots`) senza
aggiornare il conteggio dichiarato nei riferimenti testuali.

Lasciare l'ambiguità nei task implementativi rischierebbe che il loop autonomo
crei 17 modelli e ne ometta 3, con fallimento a cascata su migration e test.

## Decisione

**Il DDL §3.2 è la fonte autoritativa.** Il database ha **20 tabelle**, e quindi 20
modelli SQLAlchemy. L'elenco canonico è:

1. experiments
2. models
3. prompt_templates
4. context_snapshots
5. context_build_runs
6. runs
7. llm_invocations
8. decisions
9. decision_actions
10. account_snapshots
11. positions
12. orders
13. fee_events
14. funding_events
15. cost_events
16. tax_sim_periods
17. outcomes
18. baseline_configs
19. baseline_equity_snapshots
20. errors

I task M1 (`M1-T06a..i`) creano tutti e 20 i modelli, raggruppati per sottosezione
DDL e ordinati per dipendenza FK. Il test `M1-T11` (`test_db_migrations.py`)
verifica che `alembic upgrade head` crei **20** tabelle, non 17.

## Conseguenze

### Positive
- Nessuna ambiguità per il loop: il task dice esplicitamente "20" con elenco.
- Migration e test allineati al DDL reale.

### Negative
- Il testo del PRD (§3.1, §12) resta con "17" — non lo modifichiamo perché il PRD
  è frozen (tag `prd-v2-frozen`). Questo ADR è la fonte di verità correttiva.

### Neutre
- Chi legge il PRD in futuro deve sapere che §3.2 (DDL) prevale sul conteggio
  testuale. Questo ADR lo documenta.

## Alternative considerate

### Alternativa A: modificare il PRD per correggere "17" → "20"
- Scartata: il PRD è frozen come pre-registrazione scientifica (commit `22d3119`,
  tag `prd-v2-frozen`). Modificarlo violerebbe il significato del freeze. La
  correzione vive in questo ADR, tracciabile.

### Alternativa B: lasciare l'ambiguità e decidere in implementazione
- Scartata: il loop autonomo (Sonnet) ha bisogno di task non ambigui. "17 o 20?"
  è esattamente il tipo di decisione che non va lasciata a un'iterazione di loop.

## Test gating

`tests/integration/test_db_migrations.py` verifica che lo schema applicato contenga
esattamente le 20 tabelle elencate sopra (count + nomi).

## Propagazione

- [x] Documentata la decisione in questo ADR
- [ ] M1-T06a..i creano i 20 modelli (eseguito dal loop)
- [ ] M1-T11 verifica 20 tabelle in migration
- [ ] (PRD non modificato: frozen)
