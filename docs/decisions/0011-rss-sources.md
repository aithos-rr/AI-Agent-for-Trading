# ADR-0011: RSS News Sources — Count e Lista Definitiva

**Data**: 2026-06-14
**Status**: accepted
**Milestone**: M3 (collectors/news.py)
**PRD reference**: §15.4 (D5), §6.3 (NewsItem), §7.2 (BaseCollector), §10.1 (O3)
**Closes deferral**: D5

## Contesto

Il PRD §15.4 ha deferito esplicitamente due decisioni legate al news collector:

1. **Numero di news items per tick** — da calibrare in M3 sul token budget del prompt.
2. **Lista RSS sources definitiva** — CryptoPanic e CoinDesk erano indicate come candidate, ma senza impegno formale.

Il PRD nota che il `prompt_template_hash` include questi parametri e deve essere stabile
dal seed sperimentale in poi. Chiudere D5 prima di implementare il ContextBuilder (M3-T07)
è quindi obbligatorio.

Vincoli emersi dall'implementazione:
- Il PRD §7.2 specifica `timeout 8s, cached TTL 90s` per il news collector.
- `NewsItem.title` ha `max_length=300`, `.summary` ha `max_length=600` (§6.3).
- Il startup check O3 (§10.1) richiede `check_sources_reachability() → ≥1 reachable`.
- Il firewall del devcontainer blocca `cryptopanic.com` e `coindesk.com` in sviluppo;
  la connettività reale sarà verificata dallo smoke M3-T11 (human-gated).

## Decisione

### Fonti RSS (2 sorgenti)

| Chiave | URL |
|--------|-----|
| `cryptopanic` | `https://cryptopanic.com/news/rss/` |
| `coindesk` | `https://www.coindesk.com/arc/outboundfeeds/rss/` |

CryptoPanic: news aggregator crypto-specifico con sentiment segnalato dalla community,
feed pubblico senza autenticazione. CoinDesk: testata giornalistica di riferimento per
il settore, feed RSS attivo e stabile. Le due fonti coprono angolazioni diverse
(aggregazione vs editorial), riducendo il bias di una singola fonte.

### Numero di items per tick

**10 items massimi per tick**, ordinati per `published_at` decrescente (più recenti
prima), estratti dal pool combinato di tutte le fonti.

Rationale token budget:
- `NewsItem` max ≈ 300 + 600 = 900 chars ≈ 225 token/item
- 10 items → ≈ 2250 token, compatibile con il context di tutti e 4 i modelli target
- Soglia conservativa che lascia margine per il resto del `ContextBundle` e il
  `PortfolioState` model-specific

### Semantica di fallimento parziale

Il collector **tollera fallimenti di una singola fonte**: se almeno una fonte risponde,
restituisce gli items disponibili. Solleva eccezione solo se **tutte** le fonti
falliscono:
- `CollectorTimeoutError` se tutti i fallimenti erano timeout
- `CollectorSourceError` altrimenti

### Implementazione in `base.py`

Il tipo generico `BaseCollector[T]` è stato devincolato da `T: BaseModel` (rimuovendo
il bound) per supportare `list[NewsItem]` e `list[OnChainSnapshot]` come tipi di ritorno
validi. **Deviazione consapevole dal PRD §7.2**, che definisce esplicitamente
`T = TypeVar("T", bound=BaseModel)`: il bound viene rilassato perché `collect()` deve
poter restituire `list[...]`, che non è una sottoclasse di `BaseModel`. La deviazione è
registrata qui come richiesto da CLAUDE.md ("ogni scostamento dal PRD frozen → ADR").

## Conseguenze

### Positive
- Vocabolario D5 chiuso prima di M3-T07 (ContextBuilder): il `prompt_template_hash`
  può ora includere stabilmente i parametri news.
- 10 items è compatibile con il token budget di tutti i 4 provider target.
- 2 fonti eterogenee riducono il rischio di single-source bias nel contesto LLM.
- Fallimento parziale tollerato: resilienza dell'orchestrator anche se una fonte è down.

### Negative
- 10 items/tick è una soglia fissa; se il token budget venisse ridotto in futuro
  richiederebbe un nuovo ADR e modifica del `prompt_template_hash`.
- Il `sentiment_polarity` in `NewsItem` è sempre `None` a runtime (nessuna
  analisi sentiment inline): potrebbe essere popolato in M6+ con un modello dedicato.

### Neutre (trade-off accettati)
- CryptoPanic in modalità free-tier non include sentiment score nelle API, solo nel feed
  web. Accettato per la prima fase sperimentale.
- L'ordinamento per `published_at` ISO string funziona correttamente solo se le fonti
  emettono date in formato RFC 2822 parseable; fallback a `datetime.now(UTC)` per
  date malformate.

## Alternative considerate

### Alternativa A: 5 items/tick
- Pro: token budget più conservativo.
- Contro: potrebbe perdere eventi rilevanti intorno ai tick di 15 min; calibrazione
  troppo restrittiva per il volume di news crypto.
- Scartata: 10 items offre copertura ragionevole senza saturare il context window.

### Alternativa B: 20 items/tick
- Pro: copertura massima degli eventi.
- Contro: ≈4500 token solo per news, lasciando poco spazio per technical/sentiment/
  onchain nel prompt. Rischio di dilution del segnale.
- Scartata: troppo costosa in token per il beneficio incrementale.

### Alternativa C: 3 fonti (aggiungere CoinTelegraph)
- Pro: copertura editoriale più ampia.
- Contro: aumenta la latenza del collect (3 fetch sequenziali vs 2), maggiore
  probabilità di overlap di notizie (stessa storia da 3 fonti), e un'altra fonte da
  validare nel firewall al momento dello smoke M3-T11.
- Scartata: 2 fonti eterogenee sono sufficienti per M3; espandibile in M6 se
  i risultati sperimentali lo giustificano.

## Test gating

- `tests/unit/context/test_news.py` — 26 test unit con httpx mock; verifica:
  - items da entrambe le fonti (source corretto)
  - ordinamento per recency
  - cap a max_items
  - fallimento parziale (una fonte down → items dall'altra)
  - tutti falliti → `CollectorSourceError` / `CollectorTimeoutError`
  - `check_sources_reachability()` → dict corretto
  - truncation title/summary

## Propagazione

- [x] Implementato in `src/aiat/context/collectors/news.py`
- [x] Test in `tests/unit/context/test_news.py`
- [x] `base.py` aggiornato: rimosso bound `T: BaseModel` → `T` unconstrained
- [x] `docs/decisions/README.md` aggiornato
- [ ] Il `prompt_template_hash` deve includere `MAX_ITEMS_PER_TICK=10` e le due URL
      RSS al momento della generazione del seed (M6)
