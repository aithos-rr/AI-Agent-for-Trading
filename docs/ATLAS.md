# ATLAS — Mappa ragionata di AIAT·V2

**Ruolo di questo documento:** documentazione ufficiale di studio dell'intera repository, scritta per preparare la **discussione di tesi** e il **Capitolo 6**. Non è il README (quello racconta il progetto a chi passa di lì) e non è il PRD (quello è il blueprint congelato). È l'atlante: serve a chi deve *rispondere a domande* su questo sistema, non solo a farlo funzionare.

**Stato:** allineato al gate M6.2 chiuso VERDE il 2026-09-06 · **Ultima verifica contro il codice:** 2026-09-06, branch `main` a `a40f2ac`.

---

## 0. Come leggere questo documento

L'atlante è diviso in sei file. Questo è l'indice e l'inquadramento; gli altri cinque sono le parti, pensate per essere studiate una alla volta.

| Parte | File | Contenuto | Quando leggerla |
|---|---|---|---|
| — | **questo file** | §0 come leggere · §1 inquadramento | Prima di tutto il resto |
| 1 | [`ATLAS-1-STRUTTURA.md`](ATLAS-1-STRUTTURA.md) | §2 Tour della struttura + diagramma dell'architettura | Per orientarsi nel codice |
| 2 | [`ATLAS-2-RUNTIME.md`](ATLAS-2-RUNTIME.md) | §3 Il tick passo per passo · §4 I job dell'orchestrator | **La parte centrale**: se ne leggi una, questa |
| 3 | [`ATLAS-3-DATI.md`](ATLAS-3-DATI.md) | §5 Schema del database commentato | Quando la domanda riguarda i dati |
| 4 | [`ATLAS-4-DECISIONI.md`](ATLAS-4-DECISIONI.md) | §6 Digest dei 35 ADR, in ordine narrativo | Per raccontare *perché* il sistema è così |
| 5 | [`ATLAS-5-ESAME.md`](ATLAS-5-ESAME.md) | §7 Glossario · §8 Domande da discussione | Il glossario come riferimento; le domande, la sera prima |

**Convenzione sui riferimenti.** Ogni affermazione non ovvia è tracciabile: `file.py:simbolo` o `file.py:riga` per il codice, `ADR-00NN` per le decisioni (in [`decisions/`](decisions/README.md)), `PRD §X.Y` e `RESEARCH §X` per i due documenti congelati. I numeri di riga sono stati riverificati uno per uno contro `main` a `a40f2ac`: se il codice cambia, invecchiano — il simbolo resta il riferimento più stabile.

**Gerarchia delle fonti.** `PRD_V2.md` è congelato al tag `prd-v2-frozen` (commit `22d3119`): non si modifica. Ogni evoluzione successiva vive in un ADR. La verità corrente su un punto qualsiasi è quindi *PRD + tutti gli ADR che lo toccano*, non il PRD da solo — ed è la ragione per cui la Parte 4 esiste ed è lunga. **Dove il codice e il PRD divergono, in questo atlante vince il codice**, e la divergenza è segnalata con l'ADR che la giustifica: sono deviazioni deliberate e documentate, non derive.

**Che cosa questo atlante non nasconde.** Diverse sezioni segnalano difetti aperti: il ledger dei costi popolato col prezzo di fallback (Parte 2 §3, Parte 4 §6.5), la deferral D2 chiusa sulla carta ma senza call-site (Parte 3 §5, Parte 4 §6.4), l'invariante #14 non realmente presidiato dai contratti import-linter (Parte 1 §2). Sono lì di proposito: un documento di studio che elenca solo ciò che funziona non prepara a una discussione.

---

## 1. Inquadramento: che cosa è, e che cosa non è

### 1.1 La domanda

AIAT·V2 è una tesi triennale in **Filosofia e Intelligenza Artificiale** (Sapienza). Titolo provvisorio: *Comportamento decisionale di Large Language Models in un dominio finanziario ad alta variabilità: studio comparativo cross-model su crypto-perpetuals* (RESEARCH §0).

Non è una tesi di ottimizzazione finanziaria. Lo scopo dichiarato non è "fare profitto" ma **produrre evidenza** sul comportamento di LLM diversi posti nelle stesse identiche condizioni decisionali. Il claim centrale è deliberatamente sobrio:

> *In un ambiente controllato di decisione finanziaria sequenziale, diversi LLM mostrano profili decisionali, economici e giustificativi misurabilmente differenti, osservabili attraverso un dataset originale e un protocollo sperimentale riproducibile.*

Tre cose che il claim **non** rivendica, ed è bene saperle elencare a memoria: causalità fra dati di training e comportamento osservato; "model-attribuibilità" in senso forte (richiederebbe ablation sul training, fuori scope); gerarchia di intelligenza fra modelli. Il lessico usato ovunque è *model-associated under controlled prompt and context conditions* — e la cautela è deliberata, non timidezza.

Il **deliverable scientifico primario è il dataset**, non le analisi: le letture presentate in tesi sono un primo strato, non l'unico possibile (RESEARCH §0, §8.2).

### 1.2 Le tre Research Questions

- **RQ1 — Fattibilità.** In che misura un agente LLM può operare in modo economicamente sostenibile su crypto-perpetuals quando nel calcolo entrano *tutti* i costi: fee di exchange, funding rate, costi API, e una simulazione fiscale controfattuale sull'aliquota italiana? La domanda è interessante proprio perché il PnL lordo è la metrica facile e quella netta post-tax è quella onesta.
- **RQ2 — Spiegabilità dichiarata e coerenza giustificativa.** Le decisioni sono ricostruibili a posteriori in modo coerente? I profili di self-explanation differiscono fra modelli? Attenzione al livello: si misura **coerenza interna** (reasoning↔azione, confidence↔esito, segnali dichiarati↔contesto), **non fedeltà causale**. Il campo `reasoning` è una self-explanation linguistica, non una finestra sul calcolo interno (RESEARCH §7.6).
- **RQ3 — Profili comportamentali associati al modello.** A parità di prompt, portafoglio e contesto, i quattro modelli sviluppano profili sistematicamente diversi? La metrica-chiave è il **kappa di Fleiss** sulle decisioni: quattro modelli che ricevono lo stesso identico contesto sono quattro giudici che classificano lo stesso item. Kappa basso → decidono in modo indipendente; kappa alto → sono quasi intercambiabili. Il valore di kappa *è* un risultato, in entrambe le direzioni.

Le ipotesi H0/H1 sono pre-registrate in RESEARCH §2.2. Vale la pena ricordare che **"tutte le H0 confermate" è un esito legittimo e non banale**: significherebbe che i quattro modelli sono funzionalmente intercambiabili in questo dominio sotto le condizioni testate.

### 1.3 L'unità di osservazione (la scelta che vincola tutto il resto)

L'osservazione è la coppia **`(timestamp, model_id)`**, non la tripla `(timestamp, model_id, symbol)` (RESEARCH §1.0).

Operativamente: a ogni tick di 15 minuti ciascun modello fa **una sola chiamata LLM**, il cui output contiene le azioni per *tutti* i simboli osservati. Il modello decide olisticamente sul portafoglio — può ridurre l'esposizione su BTC perché sta aprendo ETH — come farebbe un trader umano. Le conseguenze sono strutturali e si vedono ovunque nel sistema:

- **nello schema DB**: `decisions` è la tabella padre, `decision_actions` la figlia 1-a-molti (Parte 3);
- **nei costi**: una chiamata per tick invece di tre, ~66% di input token in meno;
- **nella statistica**: niente race condition cross-simbolo, tutte le azioni di un tick condividono lo stesso stato di mercato;
- **nei conteggi attesi di M7**: 4 modelli × 4 settimane × 96 tick/giorno ≈ **10.752 decisioni** e ~32.256 azioni elementari.

### 1.4 Il disegno 2×2

| | **premium** | **cheap** |
|---|---|---|
| **USA** | `usa-premium` (Anthropic) | `usa-cheap` (OpenAI) |
| **CN** | `cn-premium` (Alibaba/Qwen) | `cn-cheap` (DeepSeek) |

Gli `model_id` sono **stabili e astratti**: i nomi commerciali sono un attributo, non l'identità (ADR-0020). La ragione è pratica quanto metodologica — un modello può essere deprecato o rinominato dal provider a metà esperimento, e il dataset non deve dipendere da quella stringa. Il "tier" è definito come **costo assoluto di mercato**, non come qualità percepita: è una variabile misurabile, non un giudizio.

Variabili tenute costanti fra i quattro (RESEARCH §3.2): prompt template unico e il suo hash SHA256 persistito con ogni run; le quattro sezioni fisse del contesto; il tick di 15 minuti sincronizzato; testnet sempre; ticker BTC/ETH/SOL; decision history disattivata (`inject_decision_history: bool = False`, `config/settings.py:86`); guardrail identici.

### 1.5 Che cosa questo esperimento non può dimostrare

RESEARCH §7 elenca **undici limitazioni dichiarate in anticipo**. Le più pesanti in sede di discussione: non si generalizza a mainnet con capitali significativi (niente slippage né market impact); non si generalizza ad altre frequenze né ad altri ticker; non si isola l'effetto del singolo cambio di contesto (il prompt è uno solo, l'ablation è fuori scope); non si inferisce *perché* un modello si comporta così; quattro modelli sono pochi per concludere alcunché su "USA vs CN" a livello di geografia. Sono limiti **pre-registrati**, cioè scritti prima di vedere i dati — che è la differenza fra un limite e una scusa.

A questi si aggiungono i limiti emersi *durante* l'implementazione, che gli ADR hanno prodotto e che la Parte 4 §6.5 raccoglie in un elenco unico. La Parte 5 §8 riprende gli uni e gli altri sotto forma di domande.

### 1.6 Lo stato del progetto, in una riga

M0→M5 completate; **M6.1** è stata la prima run di produzione reale (20 giorni, dataset archiviato e non comparativo); il **gate M6.2** è stato dichiarato VERDE il 2026-09-06 sul re-smoke r2; **M7** — le quattro settimane di raccolta dati di tesi — è la prossima e non è ancora partita. I due dataset già prodotti servono a validare l'infrastruttura, non a rispondere alle RQ: entrambi hanno una nota metodologica che spiega perché ([M6.1](NOTA-METODOLOGICA-M6.1.md), [M6.2-r2](NOTA-METODOLOGICA-M6.2-R2.md)), e i criteri del gate con il loro esito stanno in [`M6.2-PLAN.md`](M6.2-PLAN.md).
