# ADR-XXXX: <Titolo breve della decisione>

**Data**: YYYY-MM-DD
**Status**: proposed | accepted | superseded by ADR-YYYY | deprecated
**Milestone**: M<n> (vedi `PRD_V2.md` §12)
**PRD reference**: §X.Y (sezione del PRD che viene estesa o modificata)
**Closes deferral**: D<n> | none (se chiude una deferred decision di PRD §15.4)

## Contesto

Quale problema ha portato a questa decisione? Cosa nel PRD V2 è ambiguo,
incompleto, o non più applicabile alla realtà dell'implementazione?

Cita le sezioni del PRD V2 rilevanti, decisioni del PRE_PRD, e qualsiasi
evidenza emersa durante lo sviluppo (smoke test, comportamento provider,
ecc.) che giustifica la decisione.

## Decisione

Cosa abbiamo deciso, in concreto. Una decisione, non un menu di opzioni.

Sii specifico: nomi di funzioni, parametri di configurazione, tabelle DB,
moduli toccati.

## Conseguenze

### Positive
- ...

### Negative
- ...

### Neutre (trade-off accettati)
- ...

## Alternative considerate

### Alternativa A: <nome>
- Pro: ...
- Contro: ...
- Scartata perché: ...

### Alternativa B: <nome>
- Pro: ...
- Contro: ...
- Scartata perché: ...

## Test gating

Quale test verifica che la decisione sia rispettata in CI? (Se nessuno,
giustifica.)

## Propagazione

Quali parti del codice/PRD vanno aggiornate in seguito a questa decisione?
Checkbox per tracking:

- [ ] Aggiornare `PRD_V2.md` §X.Y con riferimento a questo ADR
- [ ] Aggiornare `CLAUDE.md` se è una regola operativa per Claude Code
- [ ] Implementare in `src/aiat/<modulo>.py`
- [ ] Test in `tests/<layer>/test_<scope>.py`
- [ ] Migration Alembic se cambia DDL
