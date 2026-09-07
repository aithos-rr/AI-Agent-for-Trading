# Nota metodologica — Re-smoke M6.2 «r2» (esperimento 77777777-7777-7777-7777-777777777777)

**Stato:** definitivo · **Periodo coperto:** 2026-08-04 → 2026-08-24 · **sha unico dei 6 servizi:** `750bd8c` (tag `m6.2-gate-r2`) · **Esito gate:** **VERDE**, dichiarato il 2026-09-06 · **Ruolo del dataset:** smoke test infrastrutturale (gate M6.2, seconda esecuzione). Questo dataset NON è il dataset di tesi: la raccolta dati per le RQ avverrà su un esperimento nuovo (M7). Nota gemella: [`NOTA-METODOLOGICA-M6.1.md`](NOTA-METODOLOGICA-M6.1.md); criteri di uscita: [`M6.2-PLAN.md`](M6.2-PLAN.md).

> **Provenienza delle cifre.** Tutti i numeri di questa nota (7.580 run, tassi di
> successo per modello, 429 outcomes, `closed=5` / `still_open=0`) provengono da query
> read-only sul **Postgres di produzione** e dai log Railway, non da artefatti versionati:
> il repo non contiene alcun record della run r2 (l'ultimo commit su qualunque ref è
> `750bd8c`, del 2026-08-04). Non sono riproducibili da questo repository — vedi §8, punto 1.

## 1. Perché una seconda esecuzione

Il primo smoke M6.2 (esperimento `66666666-…`, quello nominato in `M6.2-PLAN.md` §0) è
**fallito**: il gate è stato dichiarato **rosso** il 2026-08-04. La causa radice è
documentata in [ADR-0039](decisions/0039-experiment-scoped-open-position-lookups.md):
`PositionsRepository.list_open_for_model` filtrava per `model_id` + `closed_at IS NULL`
**senza** `experiment_id`, quindi le righe lasciate aperte da M6.1 (esperimento `5555…`,
stessi `model_id` e stessi wallet) erano visibili allo smoke. Effetto a cascata: shift FIFO
delle chiusure, ~147 righe `model_close` contaminate, 7 zombie permanenti non recuperabili
dal reconciler.

Il fix è a livello di repository (`experiment_id` obbligatorio e keyword-only) e l'ADR-0039
prescrive esplicitamente le condizioni del re-smoke: **`experiment_id` nuovo** e **wallet
flat all'avvio**. Entrambe sono state rispettate. La run r2 è quindi il primo dataset
prodotto con *entrambi* i fix root-cause in produzione: ClosureReconciler
([ADR-0038](decisions/0038-closure-reconciler-orchestrator-t4b.md)) e scoping per
esperimento (ADR-0039).

**Nomenclatura:** in questa nota `r1` = smoke su `6666…` (rosso, 2026-08-04),
`r2` = smoke su `7777…` (verde, 2026-09-06). Il dataset r1 resta archiviato nel DB come
cronaca di sviluppo; è throwaway per costruzione (ADR-0039) e non va usato per nulla.

## 2. Finestra di valutazione del gate

`M6.2-PLAN.md` §3 pre-registra i criteri C1-C9 su **uno smoke di 48 ore**. La finestra di
valutazione del gate è quindi il segmento **2026-08-04 → 2026-08-06**, l'unico intervallo
in cui tutti e quattro i modelli erano operativi. In quella finestra il tasso di run
`success` è **~96-98% per modello** e i criteri di uscita risultano soddisfatti.

Tutto ciò che segue il 2026-08-06 è un'**estensione non pianificata**: i servizi sono stati
lasciati in esecuzione altri 18 giorni. Non è la finestra di gate, e non va letta come tale
— è però il pezzo di evidenza più utile prodotto dalla run (§4).

Va detto in chiaro, perché un lettore esterno lo noterebbe: **sull'arco completo dei 20
giorni C1 non è soddisfatto** per due modelli su quattro, e la causa è esogena (§3). Il
gate è verde sulla finestra pre-registrata, non sui 20 giorni. Il PRD §12 (DoD di M6)
chiede ≥90% di run `success` sui tick schedulati e 48 ore *senza intervento manuale*:
entrambe le condizioni valgono sulla finestra 04→06/08, nessuna delle due vale
sull'aggregato dei 20 giorni. Vedi §8 per ciò che ne consegue.

## 3. Cronologia della run

**04/08 → 06/08 — finestra pulita.** Quattro modelli attivi, `git_commit_sha` identico in
tutti i run (`750bd8c`, precondizione P6). Successo ~96-98% per modello.

**07/08 — esaurimento credito API (evento esterno).** I crediti API di **OpenAI** e
**Anthropic** si esauriscono. È lo stesso evento che aveva zoppicato M6.1 (§2.1 della nota
gemella) e che la precondizione **P1** di `M6.2-PLAN.md` era stata scritta per prevenire:
P1 verifica il credito *all'avvio*, non la sua capienza per una run di durata indefinita.
Su 48 ore avrebbe retto; su 20 giorni no.

**09/08 → 24/08 — due modelli fermi.** `usa-cheap` e `usa-premium` falliscono ~100% dei
tick. I fallimenti sono **classificati correttamente**: righe `errors` con `error_kind`
`LLMError` / `LLMRateLimitError` e `runs.failure_stage` valorizzato secondo il vocabolario
chiuso di [ADR-0034](decisions/0034-failure-stage-vocabulary.md). Nessun fallimento
silenzioso, nessun run fallito senza riga `errors` corrispondente.

**Tassi su tutto l'arco (20 giorni):**

| Modello | Provider | Successo 20gg | Note |
|---|---|---|---|
| `cn-cheap` | DeepSeek | **96,5%** | attivo per l'intero periodo |
| `cn-premium` | Qwen | **97,1%** | attivo per l'intero periodo |
| `usa-cheap` | OpenAI | ~100% failed dal 09/08 | credito esaurito |
| `usa-premium` | Anthropic | ~100% failed dal 09/08 | credito esaurito |

Totale **7.580 run** persistiti. Controprova aritmetica: lo scheduler gira ai minuti
0/15/30/45 → 96 tick/giorno × 4 modelli × 20 giorni = 7.680 run schedulati; 7.580 è il
98,7% — cioè la persistenza dei run è stata pressoché completa, contando che **anche i run
falliti sono righe `runs`**. È il dato che rende leggibile il resto della nota.

## 4. Stress-test involontario: che cosa ha dimostrato

L'estensione a 20 giorni ha prodotto una condizione che nessun test pianificato avrebbe
riprodotto: **due agenti su quattro in blackout permanente, gli altri due operativi, per
16 giorni consecutivi**, con posizioni aperte e trigger SL/TP armati sul venue.

- **Zero posizioni zombie.** Contro le 5 in 20 giorni di M6.1 (nota gemella §2.2). È la
  validazione sul campo di ADR-0038: il bookkeeping delle chiusure non dipende più né dal
  successo del run dell'agente né dall'ordine degli step nel tick.
- **Isolamento perfetto.** Nessuna contaminazione cross-model né cross-experiment,
  malgrado wallet e `model_id` condivisi con i due esperimenti archiviati (`5555…`,
  `6666…`). È l'effetto voluto di ADR-0039: le righe di esperimenti archiviati sono
  invisibili **per costruzione**, non per disciplina operativa.
- **Degradazione pulita.** Un provider che smette di rispondere non corrompe il dataset:
  produce righe `runs` fallite classificate, e nient'altro. Il confine fra fallimento LLM
  (step [5]) ed esecuzione (step [8]) ha tenuto per 16 giorni di fallimenti continui —
  la proprietà che [ADR-0037](decisions/0037-schema-failure-behavioral-no-retry.md) aveva
  argomentato in teoria.

Questa è, di fatto, la parte più informativa della run: la finestra pulita dice che
l'infrastruttura funziona, i 18 giorni successivi dicono che **regge quando qualcosa si
rompe**.

## 5. Chiusura e riconciliazione post-mortem (24/08 → 06/09)

I servizi sono stati fermati il **24/08** con **5 posizioni aperte** sui due modelli CN. I
trigger SL/TP restano armati sul venue anche a servizi spenti: tutte e cinque sono state
**chiuse on-chain da SL/TP entro il 25/08**, senza che alcun processo le registrasse.

In M6.1 questo era esattamente lo scenario che generava zombie permanenti. Qui no: il
**2026-09-06**, con un redeploy dedicato, il `ClosureReconciler` (ADR-0038) le ha
bookkeppate **al primo tick**:

- `closed=5`, `still_open=0`;
- **timestamp storici fedeli** — le chiusure sono state registrate con l'istante del fill
  on-chain (24-25/08), non con l'ora del redeploy;
- bilancio finale del dataset: **429 chiusure = 429 outcomes** (criterio C4).

È la dimostrazione end-to-end che il fix T4b non dipende dalla continuità del servizio: una
chiusura avvenuta 12 giorni prima, a infrastruttura spenta, viene ricostruita dai fill
on-chain al primo tick utile. Nota che `ClosureReconcileResult` è un oggetto di
osservabilità e **non è persistito** (`closure_reconciler.py`): i valori `closed=5` /
`still_open=0` vengono dal log Railway; l'effetto durevole verificabile a DB sono le 5 righe
`positions` dell'esperimento `7777…` che acquisiscono `closed_at`, e l'identità 429 = 429.

**Nota di metodo:** il redeploy del 2026-09-06 è un **intervento manuale** ed è avvenuto
33 giorni dopo l'avvio della run. Non ricade nella finestra di gate (§2) e non contamina i
criteri, ma va registrato: il PRD §12 chiede 48 ore *senza intervento manuale*, condizione
soddisfatta nella finestra 04→06/08 e non oltre.

## 6. Anomalia aperta: burst `ChainDivergence` 15-22/08 su `cn-cheap`

Fra il **15/08** e il **22/08** la detection DB↔chain
([ADR-0025](decisions/0025-flip-atomicity-and-reconciliation.md)) ha prodotto segnalazioni
`ChainDivergence` concentrate su `cn-cheap`, in quattro raggruppamenti (**33 / 10 / 68 / 5**
segnalazioni). Nessun altro modello è coinvolto.

**Stato: TBD, diagnostica in corso.** Segnaposto esplicito, non una spiegazione. Ciò che si
può già dire:

- è **fuori dalla finestra di valutazione del gate** (04→06/08), quindi non incide sui
  criteri come valutati;
- le due cause note di divergenza erano entrambe chiuse nel codice della run r2 — T4b
  (ADR-0038) e leakage cross-experiment (ADR-0039, che restituisce alla detection un
  confronto sul solo esperimento in corso). Quindi **non è nessuna delle due**;
- il criterio **C6** di `M6.2-PLAN.md` è scritto in forma assoluta («Nessun
  `ChainDivergence` nelle 48h. Qualsiasi occorrenza = gate rosso e indagine»), mentre il
  suo titolo dice «zero divergenze **non spiegate**». Sulla finestra di 48h C6 è
  soddisfatto; sull'estensione no, e finché la diagnosi è aperta le segnalazioni sono per
  definizione *non spiegate*.

**Questo è il residuo che va chiuso prima di M7**, con un ADR se emerge una decisione di
design, o con un'annotazione in questa nota se si rivela un falso positivo della detection.
Non è opportuno derubricarlo: è l'unico segnale non spiegato prodotto dalla run.

## 7. Limiti d'uso del dataset

**Nessuna analisi comparativa fra modelli va condotta su r2.** Due modelli su quattro sono
assenti per 16 dei 20 giorni, per una causa esterna e asimmetrica: qualsiasi confronto di
attività, costo o performance è strutturalmente sbilanciato — lo stesso identico limite già
dichiarato per M6.1.

Il valore di r2 è **infrastrutturale**: dimostra che il sistema produce un dataset completo,
riconciliato con lo stato on-chain e resiliente ai fallimenti di provider. Sul comportamento
dei modelli non dice nulla di utilizzabile.

Vale inoltre la deroga già registrata nella nota M6.1 §3: lo smoke ha girato sui **saldi
residui non uniformi** di fine M6.1, non sui $1.000 uniformi della precondizione P5 —
decisione esplicita di contenimento costi. Il gate misura correttezza infrastrutturale, non
performance; M7 partirà con wallet nuovi finanziati a $1.000.

**Costi LLM da ricalcolare.** Alla dimensione *costo* si aggiunge un difetto scoperto il
2026-09-06 e corretto in `12b2329`: `llm/factory.py` cercava il listino con `model_name_api`
mentre `model_pricing.yaml` è indicizzato per `model_id` (ADR-0020), e la lookup fallita
degradava in silenzio a un prezzo di fallback. Ogni riga `cost_events` di questo dataset porta
quindi **1,00 / 5,00 / 0,00 USD per 1M token** invece del listino reale del modello, in
`cost_usd` e in `pricing_snapshot`. I dati **non sono stati riparati** (dataset archiviato,
stessa politica delle altre anomalie): qualunque cifra di costo va **ricalcolata dai token**,
che sono registrati correttamente in `llm_invocations`. Il costo è una variabile dipendente di
RQ1, quindi il punto non è di sola osservabilità.


## 8. Cosa resta prima di M7

1. **Batteria di query C1-C9 non versionata.** `M6.2-PLAN.md` §4 prevede
   `scripts/gate_check.sql` «o equivalente»; il file non esiste (`tools/gate_check.sh` è il
   runner lint/mypy/pytest delle milestone, un'altra cosa). Le query che hanno prodotto il
   verdetto verde non sono nel repo, quindi l'esito **non è riproducibile né verificabile da
   terzi** — relatore incluso. Da committare prima di M7, che dovrà rifare la stessa
   verifica.
2. **Diagnosi del burst `ChainDivergence`** (§6).
3. **Credito API su tutti e 4 i provider**, con capienza dimensionata sulle **4 settimane**
   di M7 e non solo sull'avvio: è la seconda volta che questo evento degrada una run.
   P1 va riformulata di conseguenza.
4. **Wallet nuovi a $1.000** e **`experiment_id` nuovo** (ADR-0039: il wallet HL è condiviso,
   quindi M7 deve partire da wallet flat).
5. **Auto-repair delle divergenze DB↔chain**: resta deferito (ADR-0025, criteri invariati).
   Il trigger dichiarato era «post-M6.2», ora raggiunto: serve una decisione esplicita —
   schedularlo o confermare il deferral per M7.
6. **Spot-check `reasoning_tokens=0`** su Opus thinking-only (C9, nota M6.1 §2.6): previsto
   «al primo tick dello smoke», non risulta registrato per nessuna delle due esecuzioni.
7. **`scripts/export_dataset.py`**, richiesto dal DoD di M6 nel PRD §12 (e fra i deliverable
   di M7), non esiste: la verifica del DoD come letteralmente scritta non è stata eseguita.
   Da committare, o da sostituire dichiarando la batteria C1-C9 come verifica equivalente.

---

*Documento di chiusura del gate M6.2. Le decisioni tecniche che il gate certifica sono in
[ADR-0038](decisions/0038-closure-reconciler-orchestrator-t4b.md) e
[ADR-0039](decisions/0039-experiment-scoped-open-position-lookups.md).*
