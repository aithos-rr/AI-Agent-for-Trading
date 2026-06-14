# ADR-0013: Semantica dati onchain + robustezza news (post-smoke reale M3-T11)

**Data**: 2026-06-14
**Status**: accepted
**Milestone**: M3 (vedi `PRD_V2.md` §12)
**PRD reference**: §6.3 (OnChainSnapshot), §7.2 (collectors), §15.4 (D5 / ADR-0011)
**Closes deferral**: none (raffina §6.3 e ADR-0011 con evidenza dello smoke reale)

## Contesto

Lo smoke reale M3-T11 (collector contro le API HL/F&G/RSS vere, eseguito fuori dal
firewall del devcontainer) ha rivelato tre discrepanze tra i mock e la realtà che i
test unit non potevano cogliere:

1. **`funding`**: l'endpoint HL `/info` (`metaAndAssetCtxs`) ritorna il funding rate
   **ORARIO**. Valore reale osservato: `0.0000125` identico su BTC/ETH/SOL = la baseline
   interest di Hyperliquid (0.01% per 8h distribuita su base oraria). Lo salvavamo
   tale e quale in `OnChainSnapshot.funding_rate_8h`, cioè con unità errata (1h in un
   campo "8h"). Il PRD §6.3 assume un periodo 8h (cfr. §3.2.2 riga 330).

2. **`long_short_ratio`**: HL `/info` **non espone** un long/short ratio globale.
   L'implementazione M3 lo derivava da `impactPxs` (bid/ask impact) → valore privo di
   significato (~1.0, osservato `0.9999...` su tutti gli asset). Il ctx grezzo espone
   però `premium` (perp vs oracle, es. BTC `-0.0002442114`): un segnale direzionale
   **reale** (+ = pressione long, − = short).

3. **News RSS**: entrambe le fonti D5 (ADR-0011) falliscono sul reale: CoinDesk
   risponde **HTTP 308** (redirect non seguito da httpx di default); CryptoPanic
   restituisce XML **non well-formed** (`mismatched tag`) che `ElementTree` (strict)
   rifiuta.

## Decisione

**Funding (F5)** — `context/collectors/onchain.py`: `funding_rate_8h = _to_decimal(ctx["funding"]) * 8`.
Si mantiene il nome di campo `funding_rate_8h` (coerente con §6.3) rendendolo
semanticamente corretto (rate equivalente 8h). `Decimal * 8` preserva l'esattezza (inv #12).

**Premium (F4)** — si rinomina il campo `OnChainSnapshot.long_short_ratio` → **`premium: Decimal`**
(`domain/schemas.py`) e si salva `ctx["premium"]` grezzo (signed). Rimossa del tutto la
derivazione da `impactPxs`. **Deviazione consapevole dal PRD §6.3** (che nomina
`long_short_ratio`): registrata qui perché HL non fornisce il dato originale e `premium`
è il segnale direzionale reale disponibile.

**News (D5)** — `context/collectors/news.py`:
- `follow_redirects=True` su GET e HEAD (risolve il 308 CoinDesk).
- parsing a due livelli: `_parse_rss_strict` (ElementTree); su fallimento, fallback
  `_parse_rss_lenient` (basato su `html.parser.HTMLParser`, stdlib, tollerante a markup
  malformato — risolve CryptoPanic). Se anche il fallback non estrae item, si rilancia
  `CollectorSourceError` (la tolleranza a fallimento parziale di ADR-0011 resta invariata).

## Conseguenze

### Positive
- Il funding somministrato all'LLM ha unità corretta e confrontabile (8h).
- `premium` è un segnale onchain reale e informativo, non rumore.
- Il NewsCollector funziona contro le fonti reali (CoinDesk via redirect; CryptoPanic
  via fallback lenient).
- Nessuna dipendenza nuova: solo stdlib (`html.parser`).

### Negative
- `premium` cambia il nome di campo rispetto al PRD §6.3 (deviazione documentata).
- Il parser lenient è best-effort: su feed estremamente corrotti può estrarre meno item.

### Neutre (trade-off accettati)
- `liquidations_24h_usd` resta una proxy (`dayNtlVlm * 0.001`): HL non espone liquidazioni
  globali via `/info`. Non in scope di questo ADR (invariato da M3-T05).
- `funding_rate_8h` come ×8 dell'orario assume costanza nell'orizzonte 8h (approssimazione
  standard per il contesto LLM).

## Alternative considerate

### A: rinominare `funding_rate_8h` → `funding_rate_1h`
- Pro: nessuna moltiplicazione.
- Contro: deviazione di schema + nome non allineato all'assunzione 8h del PRD.
- Scartata perché: ×8 mantiene il contratto §6.3 ed è la convenzione perp più comune.

### B: `long_short_ratio` come placeholder costante (1.0)
- Pro: nessun cambio di schema.
- Contro: somministra all'LLM un falso segnale "bilanciato" su tutti i modelli.
- Scartata perché: `premium` è un segnale reale; meglio onestà del dato (validità scientifica).

### C: dipendenza `feedparser`/`lxml` per le news
- Pro: parser robusti e maturi.
- Contro: nuova dipendenza (richiede approvazione + ADR), superflua per 2 sole fonti.
- Scartata perché: il fallback `html.parser` stdlib copre il caso reale osservato.

## Test gating
- `tests/unit/context/test_onchain.py`: `funding_rate_8h == funding*8`, `premium` dal ctx,
  `premium` mancante → `CollectorSourceError`.
- `tests/unit/context/test_news.py`: `follow_redirects=True` su GET; feed malformato
  recuperato dal fallback lenient.
- `tests/unit/domain/test_pydantic_serialization.py` + builder/integration: schema `premium`.

## Propagazione
- [x] Implementato in `src/aiat/context/collectors/onchain.py`, `news.py`, `domain/schemas.py`
- [x] Test aggiornati/aggiunti (onchain, news, serializzazione, builder, integration)
- [ ] `PRD_V2.md` §6.3 resta frozen; la deviazione `long_short_ratio`→`premium` è tracciata qui
- [ ] Verifica finale via re-run smoke reale M3-T11 (umano, fuori firewall)
- [ ] Il seed M7 deve includere la semantica `premium`/`funding_rate_8h` nel `prompt_template_hash`
