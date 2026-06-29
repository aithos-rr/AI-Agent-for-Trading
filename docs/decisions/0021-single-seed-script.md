# ADR-0021: Seed unico (`seed_experiment.py`) invece di due script separati

**Data**: 2026-06-29
**Status**: accepted
**Milestone**: M5-T14 / M6 (prerequisito di avvio servizi), chiude la struttura di seed di D1
**PRD reference**: §11.x seed (riga ~267), §10.1 startup check A5 (prompt template registrato)
**Closes deferral**: none

## Contesto

Il PRD nomina **due script di seed separati**:
- uno che popola `experiment` + i 4 `models` + i `baseline_configs`;
- uno (`register_prompt_template.py`) che registra il `prompt_template` (A5).

Entrambi devono concordare sul **`prompt_template_hash`**: l'agent legge
`AIAT_PROMPT_TEMPLATE_HASH` dal suo `.env`, e lo startup check **A5** (`lifecycle.py`) fallisce
se quell'hash non corrisponde a una riga `prompt_templates`. Il `rendered_prompt_hash` e la
comparabilità cross-model (inv #6) dipendono da questo hash stabile.

Con due script, il calcolo dell'hash (sha256 di `template_text` + `confidence_def` +
`controlled_signals`) vivrebbe in **due posti** o dovrebbe essere passato a mano tra i due:
se divergono — anche per una differenza banale (es. uno strippa il commento BOZZA del file e
l'altro no, una normalizzazione di whitespace diversa) — A5 fallisce all'avvio con un errore
oscuro ("prompt template … not registered") difficile da diagnosticare.

## Decisione

**Un solo script, `scripts/seed_experiment.py`, registra TUTTO** in una sequenza coerente e
idempotente (get-or-create), in un'unica transazione:

1. `experiment` (get-or-create per `name`);
2. i 4 `models` (id strutturali ADR-0020; `model_name_api` placeholder, congelati al seed M6);
3. il `prompt_template`: `template_text` (da `src/aiat/prompts/trading_v1.md`, commento BOZZA
   strippato), `confidence_def` (estratto verbatim da RESEARCH §2.1), `controlled_signals`
   (`sorted(CONTROLLED_SIGNALS)`), con **`sha256_hash` calcolato una sola volta** dallo stesso
   `template_text` che viene scritto nel DB — quindi l'hash e la riga registrata non possono
   divergere per costruzione;
4. i 3 `baseline_configs` (A10: `buy_and_hold`, `cash`, `naive_momentum_ema_20_50`) via
   `BaselineRepository.register_baseline_config`.

Lo script stampa la riga esatta `AIAT_PROMPT_TEMPLATE_HASH=<hash>` da incollare nel `.env`
degli agent (e supporta `--dry-run` per ottenere l'hash senza scrivere sul DB).

## Motivazione

Due script che devono produrre/condividere il `prompt_template_hash` sono **fragili**: una
divergenza silenziosa rompe A5 con un errore oscuro all'avvio del servizio. Un **seed unico**
elimina alla radice il rischio di hash disallineato — c'è una sola fonte di verità per
`(template_text, hash)`. `register_prompt_template.py` si potrà **estrarre in futuro** se
servirà ri-registrare un nuovo template senza ri-seedare tutto (caso non attuale).

## Conseguenze

### Positive
- Impossibile, per costruzione, avere `prompt_template_hash` ≠ riga `prompt_templates`.
- Un solo punto di seed, idempotente: rilanciarlo non duplica (get-or-create) e converge.
- L'hash è ottenibile senza DB (`--dry-run`), così il `.env` si prepara prima di avviare.

### Negative / Note
- **Deviazione minore dal PRD** (riga ~267): implementativa, non architetturale — non cambia
  lo schema DB né il contratto di sistema, solo *quanti* script fanno il seed. Documentata qui
  e nel docstring di `scripts/seed_experiment.py`.
- Se in futuro serve registrare un template nuovo a esperimento già seedato, andrà estratto un
  `register_prompt_template.py` dedicato (riusando le stesse funzioni pure dello script).

## Test gating

Nessun test automatico nuovo: lo script è uno strumento operativo (gira fuori dai servizi,
contro un DB reale). Verificato in-container: `--dry-run` produce il piano + l'hash
(`c0bf92c3…`) senza toccare il DB; `mypy`/`ruff` clean. La validazione end-to-end (seed reale +
avvio servizi) è M5-T14 / M6 (human-gated, in WSL).

## Propagazione

- [x] `scripts/seed_experiment.py` (seed unico idempotente)
- [x] `src/aiat/prompts/trading_v1.md` (template v1 ratificato, commento BOZZA rimosso)
- [x] `src/aiat/py.typed` (marker PEP 561: type-check dello script che importa `aiat`)
- [x] Indicizzato in `docs/decisions/README.md`
- [ ] `register_prompt_template.py` separato → solo se/quando servirà (non ora)
- [ ] Seed reale + avvio servizi → M5-T14 / M6 (WSL, human-gated)
