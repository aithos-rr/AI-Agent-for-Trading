# ADR-0019: Context su `settings.network` (testnet) + fix tick job zero-arg

**Data**: 2026-06-29
**Status**: accepted
**Milestone**: M4/M3 (wiring orchestrator), prerequisito di M3-T11 e M5-T14
**PRD reference**: §7.1, §4.1, §6.3; invarianti #9 (testnet), #13 (context byte-identical cross-model)
**Closes deferral**: none

## Contesto

Preparando lo smoke **M3-T11** (orchestrator reale) sono emersi due problemi nel wiring di
produzione del role `context_orchestrator` (i mattoni — ContextOrchestrator, ContextBuilder, i
6 collector — erano completi e testati, ma **non assemblati** nel tick job; stesso pattern di
M4-T08: il loop ha saltato l'assemblaggio finale dietro un task human-gated).

### Problema 1 — confound di rete tra collector

`TechnicalCollector` ha `base_url` **default = mainnet** (`technical.py` `_HL_BASE_URL =
https://api.hyperliquid.xyz`), mentre `HLPublicInfoClient` (onchain) riceve
`network=settings.network` (testnet). I due collector interrogano lo **stesso endpoint `/info`**
ma su **reti diverse** → il context mescolerebbe prezzi mainnet (technical) e dati testnet
(onchain). Incoerente con l'ambiente di esecuzione (gli agent operano su testnet, inv #9).

### Problema 2 — tick job invocato a zero argomenti (bug di sistema)

Lo scheduler aggiunge il job **senza `args`** (`scheduler.add_job(job, trigger=..., id=...)`),
quindi APScheduler lo invoca a **zero argomenti**. Ma:
- l'orchestrator non aveva alcun tick job (`__main__` chiamava
  `build_scheduler_for_orchestrator(settings)` senza `tick_job` → placeholder
  `_unbound_orchestrator_tick` che solleva `RuntimeError` ad ogni tick);
- l'**agent job era anch'esso rotto**: `_build_agent_tick_job` ritornava `loop.run_once`
  (firma `run_once(tick_id, scheduled_for)`), invocato a zero arg → `TypeError` ad ogni tick.

Non è mai emerso perché **i tick job sono mockati nei test** (`test_main_dispatch` patcha sia i
builder sia gli scheduler) e M5-T14/M6 non sono mai girati. È un bug che avrebbe bloccato
l'intero esperimento. Materiale di tesi: difetto di integrazione stanato solo preparando i
gate fisici, invisibile alla suite unit.

## Decisione

### (1) Rete del context = `settings.network` (testnet)

Tutti i collector di mercato leggono dalla rete in `settings.network` (testnet, inv #9):
`TechnicalCollector` riceve `base_url=_HL_TESTNET_URL` (importato da `onchain.py`),
`HLPublicInfoClient` riceve `network=settings.network`. Il default mainnet della classe
`TechnicalCollector` resta (per non rompere gli unit che passano `base_url` esplicito), ma la
**wiring di produzione passa sempre l'URL testnet**. Coerenza context↔esecuzione: il modello
decide su segnali dello stesso ambiente in cui opera.

Verificato sul campo: la testnet HL espone **201 candele 15m** per BTC/ETH/SOL → gli indicatori
tecnici sono calcolabili.

### (2) Tick job zero-arg + allineamento `tick_id` al boundary 15m

Aggiunto `scheduler.current_tick() -> (tick_id, scheduled_for)`: floor di `datetime.now(UTC)`
al boundary di 15 minuti (`minute=(now.minute//15)*15, second=0, microsecond=0`). `tick_id` =
ISO del boundary; `scheduled_for` = lo stesso istante come `datetime`.

Entrambi i tick job diventano **closure zero-arg** che usano `current_tick()`:
- `_build_orchestrator_tick_job` → `build_tick_context(tick_id, tick_at, experiment_id)`;
- `_build_agent_tick_job` → `run_once(tick_id, scheduled_for)` (non ritorna più `run_once` nudo).

**Allineamento `tick_id` = requisito di inv #13**: l'orchestrator fira a `:MM:00`, gli agent a
`:MM:30` (stesso minuto, +`agent_start_delay_seconds`), stesso quarto d'ora → il floor produce
lo **stesso `tick_id`**, quindi gli agent leggono lo snapshot che l'orchestrator ha scritto per
quel tick. Senza floor condiviso, orchestrator e agent userebbero `tick_id` diversi e gli agent
non troverebbero mai il context (missed tick perenne).

## Conseguenze

### Positive
- Il role `context_orchestrator` scrive davvero i `context_snapshots` (sblocca M3-T11).
- Il role agent esegue davvero il decision loop a ogni tick (sblocca M5-T14).
- Context coerente con l'esecuzione (un'unica rete testnet).

### Negative / Note (validità scientifica — da dichiarare nei limiti, accanto a PRD §13 slippage)
- I prezzi testnet differiscono dal mainnet (BTC ~58 USDC sulla testnet osservata): gli
  indicatori tecnici sono calcolati su un mercato a **liquidità ridotta** → **possibile maggior
  rumore di segnale**. **Irrilevante per uno studio COMPARATIVO**: tutti e 4 i modelli vedono lo
  stesso context byte-identico (inv #13). Da dichiarare nei limiti.
- `TechnicalCollector` mantiene un default `base_url` mainnet: footgun latente, mitigato dal
  fatto che l'unico costruttore di produzione passa l'URL testnet (candidato preflight: vedi
  sotto).

## Lavoro correlato / follow-up

- Preflight `_check_orchestrator_sources` (lifecycle O2) **non** verifica la raggiungibilità di
  `TechnicalCollector` (fonte dati principale del context). Candidato all'inclusione — non in
  questo ADR (non blocca il wiring).
- M3-T11 / M5-T14 restano **human-gated**: richiedono il run reale (rete + DB) e non sono
  spuntati da questo lavoro.

## Test gating

- `tests/unit/orchestration/test_scheduler.py`: unit di `current_tick` (stesso quarto → stesso
  `tick_id`; quarti diversi → `tick_id` diversi; floor ai boundary :00/:15/:30/:45).
- `tests/unit/orchestration/test_main_dispatch.py`: entrambi i ruoli costruiscono un tick job
  reale zero-arg e lo passano allo scheduler; il job orchestrator chiama `build_tick_context`,
  il job agent chiama `run_once`.

## Propagazione

- [x] `scheduler.py`: `current_tick()` + costante `_TICK_MINUTES`
- [x] `__main__.py`: `_build_orchestrator_tick_job` (nuovo) + `_build_agent_tick_job` (closure
      zero-arg) + `_main` passa il job orchestrator allo scheduler
- [x] Indicizzato in `docs/decisions/README.md`
- [ ] TechnicalCollector nel preflight O2 → follow-up (non bloccante)
- [ ] Run reale M3-T11 / M5-T14 in WSL (human-gated)
