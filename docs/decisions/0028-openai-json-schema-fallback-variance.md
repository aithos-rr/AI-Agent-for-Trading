# ADR-0028: `json_schema` diretto su OpenAI funziona ma con varianza residua sotto temp=0+seed — `fallback_used` come metrica sperimentale

**Data**: 2026-07-01
**Status**: accepted
**Milestone**: M5-T14 (verifica anticipata del confine direct-provider); materiale **RESEARCH §7**
**PRD reference**: §8.2 (`invoke_structured`, PATH1 `with_structured_output` + PATH2 fallback
freetext); ADR-0008 (dual-mode, verifica direct-provider prevista a M6), ADR-0020/ADR-0023
(determinismo cross-model), ADR-0022 (smoke M5-T14)
**Closes deferral**: none

## Contesto

M5-T14 ha fatto girare il **primo Agent su provider diretto** — **OpenAI `gpt-4.1-mini`** (gateway
`direct`, non OpenRouter). `invoke_structured` (§8.2) usa in **PATH 1**
`with_structured_output(TradeDecision, method="json_schema")`; il commento in `structured.py`
prevedeva la **verifica del percorso direct-provider a M6** (ADR-0008). La verifica è stata
**anticipata a M5-T14**.

Risultati osservati (LLM reale, prompt reale):

- **`json_schema` diretto funziona**, ma con **varianza residua** nonostante `AIAT_TEMPERATURE=0`
  + `AIAT_SEED=42`: su **8 invocazioni** dello stesso prompt reale, **~6 PATH1-diretto / ~2 via
  fallback**; **tutte e 8** hanno prodotto un `TradeDecision` valido, **0 fallimenti
  irrecuperabili**.
- Sui **2 tick reali**: **tick1** `fallback_used=true`, **tick2** `fallback_used=false`.
- `temperature=0 + seed` su OpenAI ⇒ **determinismo best-effort, non garantito**: la varianza
  residua occasionalmente produce un output che **non valida al primo colpo** (PATH1), recuperato
  dal **fallback freetext** (PATH 2 + `FALLBACK_SUFFIX`).

Questo conferma empiricamente l'assunto di ADR-0008 (i provider diretti vanno verificati) e
completa il quadro di ADR-0023 sull'asimmetria di determinismo cross-model, ora **quantificata**
su OpenAI e non solo qualitativa.

## Decisione / Implicazioni

Nessun cambiamento di codice: `invoke_structured` si comporta **come progettato** (PATH1 con
recupero PATH2). Si ratificano le seguenti implicazioni:

- **(a) `fallback_used` è una metrica sperimentale per-modello**, non un allarme. La frequenza di
  ricorso al fallback è un **indicatore di robustezza/conformità structured-output** del
  provider/configurazione — dato scientifico da raccogliere, non un errore da azzerare.
- **(b) Varianza residua sotto `temp=0+seed` va dichiarata come limite in RESEARCH §7**: parte
  dell'**asimmetria di determinismo cross-model** (ADR-0023), ora **quantificata** (~2/8 ≈ 25%
  fallback su OpenAI `gpt-4.1-mini`), non solo descritta qualitativamente.
- **(c) Nota metodologica**: in ~25% dei casi la decisione è presa con **`prompt + FALLBACK_SUFFIX`**
  (non il prompt canonico "liscio"). Va **dichiarato** nell'analisi: una frazione delle decisioni
  nasce da un prompt leggermente diverso da quello nominale.
- **(d) Qwen/DeepSeek** (stesso `OpenAICompatibleClient`) mostreranno **probabilmente** un
  comportamento di fallback analogo — **da osservare** quando ciascuno entra nello smoke (un
  confine reale alla volta, come per Anthropic in ADR-0023).

## Conseguenze

### Positive
- Confine direct-provider su `json_schema` **verificato in anticipo** (M5-T14 invece di M6): il
  percorso funziona end-to-end su OpenAI diretto, con recupero garantito dal fallback.
- `fallback_used` acquisisce **valore analitico** esplicito (robustezza per-modello) invece di
  restare un semplice flag interno.

### Negative / Limiti
- **Determinismo non garantito** anche con `temp=0+seed`: la riproducibilità esatta del singolo
  tick non è assicurata su OpenAI; l'analisi deve trattare la varianza come **caratteristica
  misurata**, non come rumore da eliminare.
- **Prompt effettivo non uniforme**: la quota di decisioni con `FALLBACK_SUFFIX` introduce una
  piccola disomogeneità nel prompt applicato — mitigata dichiarandola (c), non eliminabile senza
  rinunciare al fallback (che è la rete di sicurezza contro i fallimenti irrecuperabili).
- La stima ~25% è su **campione piccolo** (8 invocazioni / 2 tick) e su **un solo modello**: va
  raffinata su volumi maggiori a M6 e estesa a Qwen/DeepSeek (d).

### Neutre
- `structured.py` (PATH1 `method="json_schema"` + PATH2 fallback) **non modificato**: il
  comportamento osservato è quello atteso by-design. Lo scope di eventuali tuning del metodo
  structured-output per provider diretto resta **M6** (ADR-0008), non anticipato qui.

## Test gating

- Nessun nuovo test richiesto da questo ADR (documenta un'osservazione, non cambia comportamento).
- A M6: raccolta di `fallback_used` su volume maggiore per stimare la frequenza per-modello;
  osservazione del comportamento fallback su Qwen/DeepSeek diretti.

## Propagazione

- [x] Osservazione tracciata in questo ADR (verifica anticipata del confine direct-provider)
- [x] Indicizzato in `docs/decisions/README.md`
- [ ] RESEARCH §7: dichiarare la varianza residua quantificata (b) e la nota metodologica
      `FALLBACK_SUFFIX` (c) come limiti
- [ ] M6: raffinare la stima su volume maggiore; osservare Qwen/DeepSeek (d)
- [ ] (`structured.py`/schema non modificati: solo osservazione)
