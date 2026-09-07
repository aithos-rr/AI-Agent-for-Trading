# ADR-0040: Doc-sync di gate — allineamento di `RESEARCH_DESIGN.md` alle decisioni già prese (M6.2 → M7)

**Data**: 2026-09-06
**Status**: accepted
**Milestone**: M6.2 (pre-M7)
**PRD reference**: **nessuna modifica al `PRD_V2.md`**. Documento modificato: `RESEARCH_DESIGN.md` §1.1 (nota terminologica di RQ1), §3.3, §4.1, §5, §7. Contesto tecnico citato ma non toccato: PRD §4.3 (simulazione fiscale), §4.4 (baseline), §8.2 (structured output), §15.4 (bounded deferrals)
**Closes deferral**: none — registra però due decisioni su deferral aperti: **D2** (ADR-0014) resta **non cablata** per M7, e l'**auto-repair** DB↔chain (ADR-0025) resta **deferito**

## Contesto

`RESEARCH_DESIGN.md` è uno dei cinque documenti congelati al tag `prd-v2-frozen` (commit
`22d3119`, `CLAUDE.md` §"Ground truth documenti"). Modificarlo è per costruzione una deviazione,
e la regola operativa di `CLAUDE.md` («stai deviando da una sezione del PRD V2 / dai documenti
frozen? → ADR obbligatorio») impone un ADR. Questo è quell'ADR.

**Che tipo di ADR è.** Non introduce alcuna scelta nuova: **ratifica e propaga**. Ogni voce qui
sotto è una decisione già presa in un ADR precedente e già implementata in codice; ciò che manca
è la sua traccia nel documento scientifico. È un **doc-sync di gate**, eseguito nel momento in cui
il gate M6.2 è verde (`docs/M6.2-PLAN.md` §7, 2026-09-06) e prima che parta M7.

**Perché non è cosmetico.** `RESEARCH_DESIGN.md` non è un commento al codice: è l'atto di
pre-registrazione della tesi — lo dice il documento stesso in §8.2 («questo Research Design
committato in git prima dell'inizio dell'esperimento è esso stesso un atto di trasparenza
scientifica vincolante»). Una limitazione scoperta durante l'implementazione e scritta solo in un
ADR **non è pre-registrata** nel documento che la tesi presenta come pre-registrazione. E il
disallineamento è già arrivato al punto di produrre una citazione falsa:

> `docs/M6.2-PLAN.md:59` — «Determinismo cross-modello irraggiungibile (ADR-0023, **dichiarato in
> RESEARCH §7**).»

Oggi `RESEARCH_DESIGN.md` §7 (`:377-391`) elenca **undici** limitazioni e **nessuna** riguarda il
determinismo. Il piano di gate rimanda a una pre-registrazione che non esiste: un lettore esterno
— relatore o commissione — che seguisse il rimando non troverebbe nulla. È questa la ragione per
cui la propagazione va fatta ora e non "in fase di scrittura".

### Stato verificato dei sei punti (read-only sul repo, 2026-09-06)

| # | Punto | Stato verificato |
|---|---|---|
| a | §7 non contiene i limiti di ADR-0023 / ADR-0028 / ADR-0037 | `RESEARCH_DESIGN.md:381-391` = 11 voci, nessuna sul determinismo, sulla varianza dello structured output o sulla schema-compliance. Caselle di propagazione aperte: `0023:106`, `0028:85`, `0037:119` |
| b | §3.3 descrive i baseline come calcolati «a posteriori» | `RESEARCH_DESIGN.md:207`; ADR-0036 li ha spostati al calcolo **live per tick** (`src/aiat/baselines/runner.py`, step in `__main__._orchestrator_tick`) |
| c | Il 26% compare 4 volte in RESEARCH | `:70`, `:188`, `:270`, `:307` (grep esaustivo su `26`). A runtime si applica **0.33** (verifica sotto) |
| d | Perimetro del gate = 48h pre-registrate | Lettura già scritta in `docs/M6.2-PLAN.md:80-88` (§7.1) e `docs/NOTA-METODOLOGICA-M6.2-R2.md:33-50` (§2); non esiste una regola di metodo generale scritta una volta sola |
| e | Auto-repair ADR-0025: trigger «post-M6.2» raggiunto | `0025:159-166` (criteri), casella `[ ]` a `0025` in coda; nota r2 §8 punto 5 chiede una decisione esplicita |
| f | D2 (ADR-0014) chiusa sulla carta, non nei dati | `grep -rn "resolve_hold_flat\|persist_outcome" src/ scripts/` → **zero call-site di produzione** (solo `src/aiat/execution/outcome_resolver.py:144` e `src/aiat/db/repositories/outcomes.py:26`, cioè le definizioni). Le righe `outcomes` nascono solo da `PositionsRepository.close_position` (`positions.py:151`, `Outcome(` a `:323`) |

### Verifica indipendente dell'aliquota fiscale (punto c)

L'ADR-0033 dichiara 0.33 come override di config. Verificato sul codice, non sul testo dell'ADR:

- `src/aiat/config/settings.py:131` — `tax_rate_pct: Decimal = Field(default=Decimal("0.33"), ge=0, le=1)`
  su `ContextOrchestratorSettings`, con `env_prefix="AIAT_"` (`settings.py:23`) → override
  `AIAT_TAX_RATE_PCT`.
- `src/aiat/__main__.py:170` — `_build_tax_sim_job` passa `tax_rate_pct=settings.tax_rate_pct` al
  `TaxSimRunner`.
- `src/aiat/orchestration/tax_sim_runner.py:127` — il runner passa il valore **esplicitamente** a
  `compute_and_persist_period` per ogni riga.
- Quindi il default del repository (`src/aiat/db/repositories/tax_simulation.py:34`,
  `Decimal("0.26")`) e il `server_default` di schema (`src/aiat/db/models/tax_sim.py:41` e
  `alembic/versions/001_initial_schema.py:653`, `"0.26"`) **non sono mai determinanti a runtime**.
- Confermato dal test `tests/e2e/test_tax_sim_runner.py:261` (`row.tax_rate_pct == Decimal("0.33")`
  — «0.33 override, NOT the schema 0.26 default») e dal criterio di gate C5 (`M6.2-PLAN.md:37`,
  «rate 0.33»), passato in finestra.

Una precisazione che il solo ADR-0033 non rende: la simulazione fiscale esiste **solo come
aggregato di periodo** in `tax_sim_periods`. La colonna per-outcome
`outcomes.pnl_net_fee_funding_tax_sim_usd` resta `Decimal("0")` by design
(`src/aiat/db/repositories/positions.py:337-339`, coerente con ADR-0014).

## Decisione

Si modifica `RESEARCH_DESIGN.md` — e **solo** quel documento — con interventi **additivi e datati**
(`(*) 2026-09-06, ADR-0040: …`). Il testo pre-registrato **non viene cancellato né riscritto**: il
valore del documento sta nell'essere stato scritto prima, quindi ogni aggiornamento è una nota che
si affianca all'originale. `PRD_V2.md` non è toccato in alcun caso.

### (a) Tre limitazioni mancanti entrano in §7 come voci **12, 13, 14**

Numerazione progressiva dopo la voce 11, stesso stile delle esistenti (lead-in in grassetto + spiegazione + conseguenza per l'analisi):

- **12 — asimmetria di determinismo cross-model** (propaga ADR-0023). ADR-0020 prescriveva
  `temperature=0` + `seed=42` per tutti e quattro i soggetti; i modelli Anthropic thinking-only
  rifiutano `temperature` (HTTP 400 osservato in M5-T14) e non espongono `seed`/`top_p`, quindi
  `usa-premium` gira nel regime nativo non-deterministico mentre gli altri tre girano coi sampling
  param fissati. La riproducibilità esatta del singolo run non è garantita per tutti i soggetti;
  la varianza intra-modello va trattata provider-aware. Inquadramento dichiarato: i modelli sono
  confrontati nel loro **regime reale di esercizio** (condizione ecologica), non in un regime
  artificiale che alcuni non supportano.
- **13 — varianza residua dello structured output sotto `temp=0`+`seed`** (propaga ADR-0028). Su
  OpenAI diretto (`gpt-4.1-mini`) il percorso `json_schema` produce occasionalmente un output che
  non valida al primo colpo, recuperato dall'unico fallback freetext pre-registrato: ~2 invocazioni
  su 8 nella verifica M5-T14, 0 fallimenti irrecuperabili. Due conseguenze da dichiarare: il
  determinismo è **best-effort anche dove i sampling param sono accettati**, e una quota di
  decisioni nasce da `prompt + FALLBACK_SUFFIX`, cioè da un prompt leggermente diverso da quello
  nominale. `fallback_used` è per questo una **metrica** per-modello, non un allarme. La stima ~25%
  è su campione piccolo (8 invocazioni, un modello) e va raffinata sui volumi di M7.
- **14 — schema-compliance come variabile comportamentale, senza retry, con soglie differenziate**
  (propaga ADR-0037). Un tick la cui response fallisce la validazione Pydantic su **entrambi** i
  tentativi del protocollo termina `failed` con `failure_stage='llm_parse'` e non viene rigiocato:
  un retry darebbe più tentativi al modello che sbaglia di più — trattamento asimmetrico, confound
  diretto per RQ2/RQ3. Conseguenze per l'analisi: il denominatore di affidabilità **esclude** gli
  schema-failure (≥95%), con **soglia dedicata ≥85% inclusiva** per `usa-cheap` (~10% di
  irrecuperabili); la variabile misurata è la capacità di produrre output utilizzabile *entro il
  protocollo pre-registrato* (structured + un fallback), non la compliance stretta al primo colpo;
  il costo API dei tentativi falliti non entra nel cost ledger (sotto-conteggio minore, dichiarato).

### (b) §3.3 — nota datata sui baseline calcolati live

§3.3 continua a descrivere i tre baseline con i **parametri pre-registrati invariati**. Si aggiunge
una nota che registra ciò che ADR-0036 ha cambiato: **il momento del calcolo**, non i parametri.
I baseline sono calcolati **live a ogni tick** dal `context-orchestrator`
(`src/aiat/baselines/runner.py`, invocato in `__main__._orchestrator_tick`) e persistiti in
`baseline_equity_snapshots`; `scripts/compute_baselines.py` rigioca la stessa logica come
backfill/catch-up. La nota riporta anche le due precisazioni operative fissate da ADR-0036 che un
lettore dei parametri di §3.3 deve conoscere: SL/TP del baseline momentum valutati **sul close**
del tick (wick intra-candle non catturati; asimmetria dichiarata rispetto agli SL/TP intra-tick
on-chain dei modelli LLM) e fee taker `0.00045` validata sui `fee_events` reali.

### (c) Aliquota fiscale: 26% pre-registrato, **0.33 operativo**

Tutte e quattro le occorrenze del 26% (`:70`, `:188`, `:270`, `:307`) restano a testo come valore
pre-registrato e ricevono una nota datata. La nota principale sta nella nota terminologica di RQ1
(§1.1); le altre tre vi rimandano. Contenuto:

- **Valore applicato**: `0.33`, scritto esplicitamente su ogni riga `tax_sim_periods` (catena
  verificata sopra: `settings.py:131` → `__main__.py:170` → `tax_sim_runner.py:127`); il
  `server_default` 0.26 dello schema resta invariato e mai usato (nessuna migration — ADR-0033).
- **Motivazione normativa**: il regime italiano sulle plusvalenze da **cripto-attività** porta
  l'aliquota al **33%** a decorrere dal 2026, ed è il regime che questo studio assume applicabile
  al caso in esame (derivati perpetui con leva). Resta valida integralmente la riserva già scritta
  in §1.1: è una **metrica sperimentale controfattuale**, non consulenza fiscale né un calcolo
  legale; l'aliquota è un parametro di configurazione (`AIAT_TAX_RATE_PCT`), quindi l'analisi è
  ricalcolabile con un'aliquota diversa senza toccare i dati.
- **Effetto sulla pre-registrazione**: l'aliquota più alta rende l'aspettativa di §5 (pochi modelli
  net-positive) **più conservativa**, non più facile da confermare. Non è un cambio di ipotesi.
- **Nota operativa** (in §4.1): `tax_sim_26pct(...)` va letta come `tax_sim_rate(...)` con
  `rate = 0.33`; la tax-sim vive come **aggregato di periodo** (`tax_sim_periods`), mentre la
  colonna per-outcome `pnl_net_fee_funding_tax_sim_usd` resta 0 by design.

### (d) Perimetro del gate = finestra pre-registrata (regola di metodo, vale anche per M7)

Si ratifica come **regola generale** la lettura già applicata a M6.2 in `M6.2-PLAN.md:80-88` (§7.1)
e in `NOTA-METODOLOGICA-M6.2-R2.md:33-50` (§2):

> **I criteri di uscita di un gate si valutano esclusivamente sulla finestra pre-registrata nel
> piano del gate.** Per M6.2 la finestra è lo smoke di 48 ore (`M6.2-PLAN.md` §3): la finestra
> effettiva è stata **2026-08-04 → 2026-08-06** (~96-98% di run `success` per modello, tutti e
> quattro operativi). L'esecuzione proseguita per altri 18 giorni è un'**estensione non
> pianificata**: non è finestra di gate, non può essere usata per dichiarare un criterio soddisfatto
> né per dichiararlo violato. Vale simmetricamente nelle due direzioni — è la stessa regola che
> impedisce di scegliere a posteriori la finestra che conviene (§5 di RESEARCH, p-hacking).

Corollari, dichiarati insieme alla regola perché è il punto in cui è onesto dirli:

1. Sui 20 giorni **C1 non è soddisfatto** per `usa-cheap` e `usa-premium` (esaurimento credito API
   OpenAI/Anthropic dal 07/08, evento esterno). Il gate è verde sulla finestra, non sui 20 giorni:
   chi cita numeri di r2 deve dire quale dei due periodi sta citando.
2. Ciò che accade **fuori** dalla finestra non è privo di valore: è evidenza descrittiva (resilienza,
   nota r2 §4) e può **aprire un residuo** — come il burst `ChainDivergence` 15-22/08, fuori
   finestra e con diagnosi aperta (nota r2 §6). Un residuo fuori finestra non retro-tinge il gate,
   ma non è archiviabile: va chiuso prima di M7.
3. **Per M7 la regola è vincolante allo stesso modo**: la finestra di raccolta dati è quella
   pre-registrata (4 settimane, RESEARCH §6.1). Un eventuale prolungamento o un'interruzione vanno
   dichiarati come tali, e le analisi delle RQ restano ancorate alla finestra pre-registrata.

Questa regola è scritta **qui e una volta sola**: non viene duplicata nei piani di gate futuri, che
la citeranno.

### (e) Auto-repair delle divergenze DB↔chain: **resta deferito**

Il trigger dichiarato da ADR-0025 era «post-M6.2» ed è ora raggiunto (gate verde 2026-09-06). La
decisione, richiesta esplicitamente dalla nota r2 §8 punto 5, è: **il deferral è confermato per
M7**, con i **criteri di ADR-0025 invariati**. La riconciliazione resta **detection-only**: la
rilevazione netted per-coin scrive una riga `errors` `error_kind='ChainDivergence'` (con
`position_id` e `delta`) e il tick prosegue — safety net sufficiente a **identificare e filtrare a
posteriori** le righe divergenti, che è ciò che protegge il dataset.

Criteri di abilitazione, riportati **testualmente** da `docs/decisions/0025-flip-atomicity-and-reconciliation.md:159-166`
così che questo ADR sia autoconsistente:

> ### Criteri per abilitare l'auto-repair (post-M6.2)
>
> 1. Frequenza/tipologia delle divergenze osservate nello smoke (dalle righe `ChainDivergence`) note.
> 2. Regola di riconciliazione decisa per ciascun `kind` — in particolare `missing_on_chain` →
>    registrare la chiusura con quale `close_reason`/prezzo/fee? — coerente con ADR-0030/ADR-0032.
> 3. Test di riconvergenza (flip parziale → auto-repair → DB e chain riconvergono) verdi.

Motivazione della conferma, sui criteri stessi:

- **Il criterio 1 non è soddisfatto oggi.** Il burst `ChainDivergence` 15-22/08 su `cn-cheap`
  (33 / 10 / 68 / 5 segnalazioni) ha **diagnosi aperta** (nota r2 §6): tipologia e frequenza delle
  divergenze *non* sono note. Abilitare ora una scrittura correttiva significherebbe riparare
  automaticamente divergenze di cui non conosciamo la causa — cioè scrivere sul dataset scientifico
  sulla base di un'ipotesi non verificata. È l'esatto contrario di ciò che serve.
- Le **due cause radice note** sono già rimosse alla radice (ADR-0038 per T4b, ADR-0039 per il
  leakage cross-experiment), con zero zombie su 20 giorni in r2: il rischio residuo che l'auto-repair
  coprirebbe è basso, e il costo del suo errore è alto (l'auto-repair muta lo stato delle posizioni —
  la stessa famiglia di scritture che ha prodotto i bug di ADR-0027/0030).
- Sequenza corretta: **prima** la diagnosi del burst (residuo pre-M7 già tracciato), **poi**
  eventualmente l'auto-repair, con i criteri 2 e 3 di ADR-0025.

Nessuna modifica a `RESEARCH_DESIGN.md` discende da questo punto: è una decisione di processo, non
un limite del disegno sperimentale.

### (f) D2 / outcome controfattuali HOLD-FLAT: **restano non cablati per M7**

Fatto verificato: `OutcomeResolver.resolve_hold_flat` (`src/aiat/execution/outcome_resolver.py:144`)
e `OutcomesRepository.persist_outcome` (`src/aiat/db/repositories/outcomes.py:26`) esistono, sono
testati (`tests/unit/execution/test_outcome_resolver.py`, `tests/integration/test_db_repositories_outcomes.py`)
e **non hanno alcun chiamante di produzione** — il grep su `src/` e `scripts/` restituisce solo le
definizioni. Le uniche righe `outcomes` scritte a runtime nascono da
`PositionsRepository.close_position` (`positions.py:151`, `Outcome(` a `:323`), cioè da posizioni
realmente aperte e chiuse.

**Decisione: resta non cablato per M7.** La tabella `outcomes` continuerà a contenere solo posizioni
direzionali chiuse; le decisioni HOLD/FLAT non produrranno righe di outcome.

**Conseguenza dichiarata come limite di tesi** (voce **15** di §7): il **Brier score di RQ2.2 copre
le sole decisioni direzionali**. Con un 30-50% di HOLD atteso (§6.1), la calibrazione è misurata su
un sottoinsieme, con **bias sistematico a favore dei modelli che tradano di più** — che è
esattamente il difetto per cui ADR-0014 aveva scartato la propria Alternativa C. Il sistema si
comporta oggi come quell'alternativa: va detto, non aggirato. Il calcolo su HOLD/FLAT resta
possibile **offline** in fase di analisi, ricostruendo `price_at_decision`/`price_at_horizon` dai
`context_snapshots` e applicando la regola fee-hurdle di ADR-0014, che resta la regola valida e
non viene rimessa in discussione qui.

**Cablarlo è una scelta possibile, e non è presa qui.** Cosa comporterebbe, per chi la valuterà:

1. **Una migration di schema.** `outcomes.position_id` è `nullable=False` + `unique=True` con FK
   verso `positions` (`src/aiat/db/models/outcome.py:19-21`): un outcome HOLD/FLAT non ha alcuna
   posizione, quindi o si rende la colonna nullable (rivedendo la UNIQUE e i CHECK) o si inventano
   righe `positions` fittizie — opzione che inquinerebbe ogni query su posizioni, PnL ed exposure.
2. **Un job differito.** `resolve_hold_flat` richiede `price_at_horizon`, cioè un prezzo a
   `decision_time + time_horizon_min`, che al momento del tick non esiste: serve un processo che
   rilegga gli snapshot successivi e risolva gli outcome pendenti. Non è mai stato scritto.
3. **Un parametro da fissare in codice.** `HoldFlatOutcomeInput.fee_roundtrip_pct`
   (`outcome_resolver.py:54`) è obbligatorio e senza default; il valore `Decimal("0.002")` vive
   solo nel testo di ADR-0014 (`:48`).
4. **Un cambio di dataset a ridosso di M7**, cioè nuove righe sintetiche in una tabella che il resto
   dell'analisi assume popolata da sole posizioni reali — con il rischio di introdurre un difetto in
   un percorso di scrittura poche settimane prima della raccolta dati.

Il bilancio adottato: un limite dichiarato in §7 costa meno, scientificamente, di una modifica di
schema più un job batch nuovo introdotti immediatamente prima dell'esperimento. Se si decide
diversamente, serve un ADR-0041 e il cablaggio va fatto **prima** del seed di M7, mai a metà run
(cambierebbe la semantica del dataset in corso d'opera).

## Conseguenze

### Positive
- La citazione di `M6.2-PLAN.md:59` («dichiarato in RESEARCH §7») diventa **vera**: il rimando del
  piano di gate trova una pre-registrazione reale.
- §7 passa da 11 a **15** limitazioni e copre i quattro limiti sperimentali emersi
  dall'implementazione (determinismo, varianza structured-output, schema-compliance, Brier parziale).
  Sono esattamente i punti che un esaminatore attento troverebbe da solo leggendo il DB.
- Le tre caselle di propagazione «→ RESEARCH §7» aperte da mesi (`0023:106`, `0028:85`, `0037:119`)
  si chiudono, e con esse il debito documentale che il gate si portava dietro.
- L'aliquota è coerente fra codice, dati e documento scientifico: nessun lettore troverà 26% nel
  Research Design e 0.33 nelle righe `tax_sim_periods`.
- La regola sul perimetro del gate è scritta una volta sola e vale per M7: niente ri-discussione
  della finestra a risultati visti.

### Negative
- §7 si allunga, e quattro delle quindici limitazioni sono difetti **operativi** (non solo di
  disegno): il documento dice più chiaramente cosa non regge. È il prezzo, voluto, dell'onestà.
- Il Brier score parziale (voce 15) è un limite reale su una delle tre RQ, non una postilla: va
  discusso in tesi, non solo elencato.
- Il documento frozen risulta modificato dopo `prd-v2-frozen`: la sua storia va letta in git
  (`git log -- docs/RESEARCH_DESIGN.md`), non assumendo che il file corrente sia quello del tag.

### Neutre (trade-off accettati)
- Modifiche **additive e datate**: il testo pre-registrato resta leggibile accanto alla nota. Il
  documento diventa un po' più stratificato, ma resta dimostrabile *cosa* era stato scritto prima e
  *quando* è stato aggiornato — che è la proprietà da difendere.
- `PRD_V2.md` resta **intatto**: la propagazione al PRD (ADR-0033 la elenca ancora aperta per §4.3)
  non è oggetto di questo ADR e non viene fatta qui.

## Alternative considerate

### Alternativa A: non toccare `RESEARCH_DESIGN.md`, lasciare i limiti negli ADR
- Pro: il documento frozen resta bit-identico al tag; zero deviazione formale.
- Contro: la pre-registrazione resterebbe **incompleta e già citata a vuoto** (`M6.2-PLAN.md:59`);
  i limiti emergerebbero in discussione di tesi come scoperte del lettore invece che come
  dichiarazioni dell'autore. Gli ADR non sono la pre-registrazione: sono la cronaca delle decisioni.
- Scartata perché: il costo di una deviazione documentata con un ADR è incomparabilmente minore del
  costo di una pre-registrazione che non dice il vero.

### Alternativa B: riscrivere §3.3 / §4.1 / §5 con i valori correnti (0.33, baseline live)
- Pro: documento più pulito, senza note stratificate.
- Contro: **cancellerebbe la pre-registrazione**. Un documento che riporta i valori correnti come se
  fossero sempre stati quelli non è distinguibile da un documento riscritto a posteriori, e perde
  esattamente la proprietà che lo rende scientificamente utile.
- Scartata perché: contraddice §5 (la pre-registrazione come tutela contro il p-hacking).

### Alternativa C: cablare D2 (writer HOLD/FLAT) prima di M7 invece di dichiararlo come limite
- Pro: Brier score su tutte le decisioni; D2 chiusa davvero, come PRD §15.4 richiede.
- Contro: migration su `outcomes` (`position_id` NOT NULL + UNIQUE), un job batch nuovo per
  `price_at_horizon`, un parametro da fissare — tutto immediatamente prima della raccolta dati.
- Scartata **qui e ora**, non in assoluto: è la scelta descritta al punto (f), che resta aperta a un
  ADR successivo purché eseguita prima del seed di M7.

### Alternativa D: abilitare l'auto-repair DB↔chain ora che il trigger «post-M6.2» è raggiunto
- Pro: chiude un `[ ]` aperto; il dataset di M7 si auto-corregge.
- Contro: il criterio 1 di ADR-0025 (tipologia/frequenza note) **non è soddisfatto** finché il burst
  15-22/08 ha diagnosi aperta; l'auto-repair è scrittura correttiva sullo stato delle posizioni.
- Scartata perché: si riparerebbe automaticamente ciò che non si è ancora capito. Vedi punto (e).

## Test gating

Questo ADR **non cambia codice**, quindi non ha un test proprio. I presidi che restano verdi e che
rendono verificabili le affermazioni fatte qui:

- `tests/e2e/test_tax_sim_runner.py:261` — `tax_rate_pct == Decimal("0.33")` («0.33 override, NOT
  the schema 0.26 default»): è il test che rende **falsificabile** il punto (c). Se qualcuno
  rimuovesse l'override, questo test fallirebbe prima che il documento diventi falso.
- `tests/unit/execution/test_outcome_resolver.py` (24 casi) — la regola D2 resta corretta e coperta;
  ciò che manca è il chiamante, e **nessun test può fallire per la sua assenza**: è precisamente per
  questo che il punto (f) va scritto in §7 e non lasciato al codice.
- `tests/unit/baselines/test_compute.py` + `tests/integration/test_baselines_runner.py` — le
  definizioni operative citate nella nota di §3.3 (punto b).
- `tests/e2e/test_decision_loop_error_persist.py` + `tests/unit/llm/test_structured.py` — il
  comportamento descritto dalla voce 14 (schema-failure pulito, fallback singolo).

**Limite dichiarato del gating**: nessun test protegge la coerenza fra documenti e codice. La
verifica di questo ADR è documentale e va rifatta a mano al prossimo doc-sync — il modo per non
perderla è la revisione delle caselle di propagazione degli ADR, che questo ADR chiude.

## Propagazione

- [x] `RESEARCH_DESIGN.md` §7: nuove voci **12** (ADR-0023), **13** (ADR-0028), **14** (ADR-0037),
      **15** (D2 / ADR-0014)
- [x] `RESEARCH_DESIGN.md` §3.3: nota datata «baseline calcolati live per tick» (ADR-0036), parametri
      pre-registrati invariati
- [x] `RESEARCH_DESIGN.md` §1.1 / §3.3 / §4.1 / §5: quattro note datate sull'aliquota **0.33**
      (ADR-0033), 26% pre-registrato mantenuto a testo
- [x] Casella «→ RESEARCH §7» di **ADR-0023** (`0023:106`) — chiusa da questo ADR
- [x] Casella «RESEARCH §7 …» di **ADR-0028** (`0028:85`) — chiusa da questo ADR
- [x] Casella «Aggiornare `RESEARCH_DESIGN.md` §7 …» di **ADR-0037** (`0037:119`) — chiusa da questo ADR
- [x] **Auto-repair (ADR-0025)**: deferral **confermato** per M7, criteri invariati; la casella `[ ]`
      di ADR-0025 resta aperta by design (annotare qui il rimando a ADR-0040)
- [x] **D2 (ADR-0014)**: non cablata per M7, limite dichiarato in §7 voce 15; la casella
      «OutcomesRepository deve passare `fee_roundtrip_pct`» (`0014:116`) resta aperta by design
- [x] Indicizzato in `docs/decisions/README.md`
- [ ] `PRD_V2.md` — **NON toccato**, per decisione esplicita di questo ADR
- [ ] Casella «RESEARCH §7: thinking-forced-off dei provider CN» di **ADR-0029** (`0029:147`) —
      **fuori scope qui**, non chiusa: richiede una decisione a sé (vedi nota all'indice)
- [ ] Diagnosi del burst `ChainDivergence` 15-22/08 (nota r2 §6) — pre-requisito del criterio 1 di
      ADR-0025, resta residuo pre-M7
- [ ] Eventuale ADR-0041 se si decide di cablare D2 prima del seed di M7
