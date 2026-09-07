-- =============================================================================================
-- scripts/gate_check.sql — batteria di verifica dei criteri di uscita C1-C9 del gate M6.2
-- =============================================================================================
--
-- SCOPO
--   Rende riproducibile e verificabile da terzi (relatore incluso) l'esito del gate M6.2,
--   previsto da `docs/M6.2-PLAN.md` §4 punto 1 e registrato come mancante dalla nota
--   `docs/NOTA-METODOLOGICA-M6.2-R2.md` §8 punto 1. Il testo dei criteri è quello
--   pre-registrato in `docs/M6.2-PLAN.md` §3 e NON va riscritto: ogni query qui sotto
--   risponde alla domanda come è formulata lì, e il commento che la precede riporta il
--   criterio alla lettera e dice quale valore vale PASS.
--
-- READ-ONLY
--   Questo file NON scrive nulla. Contiene solo SELECT (più `SET TIME ZONE`, che agisce sulla
--   sola sessione). Non crea tabelle temporanee, non usa CTE scrivibili, non fa VACUUM/ANALYZE.
--   Può essere eseguito senza rischio sul Postgres di produzione. Se si vuole una garanzia
--   forte, lanciarlo con un ruolo di sola lettura oppure anteponendo:
--       psql "$AIAT_DATABASE_URL" -c 'SET default_transaction_read_only = on' -f scripts/gate_check.sql
--
-- COME SI LANCIA
--   Intero esperimento (default della finestra temporale):
--       psql "$AIAT_DATABASE_URL" \
--            -v experiment_id=77777777-7777-7777-7777-777777777777 \
--            -f scripts/gate_check.sql
--
--   Finestra di gate pre-registrata di 48h (§3: «valutati sulle 48h di smoke»; per r2 la
--   finestra dichiarata in §7.1 è 2026-08-04 → 2026-08-06):
--       psql "$AIAT_DATABASE_URL" \
--            -v experiment_id=77777777-7777-7777-7777-777777777777 \
--            -v window_start='2026-08-04 00:00:00+00' \
--            -v window_end='2026-08-06 00:00:00+00' \
--            -f scripts/gate_check.sql
--
--   Per salvare l'evidenza da incollare in §7 del piano (§4 punto 2: «PASS / FAIL con evidenza»):
--       psql ... -f scripts/gate_check.sql > gate_check_r2_48h.txt 2>&1
--
-- PARAMETRI
--   :experiment_id  (OBBLIGATORIO) UUID dell'esperimento. **Ogni query è scoped su di esso**:
--                   è il difetto che ha fatto fallire il gate r1 (ADR-0039 — per il DB "aperta"
--                   è uno stato globale, e una verifica non scoped ripeterebbe l'errore
--                   leggendo righe di esperimenti archiviati che condividono `model_id` e wallet).
--   :window_start   (opzionale) estremo INCLUSIVO della finestra. Default '-infinity'.
--   :window_end     (opzionale) estremo ESCLUSIVO della finestra. Default 'infinity'.
--                   Con i default, i criteri sono valutati sull'intero esperimento.
--                   La colonna temporale usata cambia per criterio ed è dichiarata nel commento
--                   (runs.scheduled_for, positions.opened_at/closed_at, errors.occurred_at, …).
--
-- NOTA SUL GUARD DEI PARAMETRI
--   Se `experiment_id` manca, il file stampa l'uso e si ferma con `\quit`, che esce con stato 0:
--   in uno script CI va controllato il messaggio, non l'exit code.
--
-- LETTURA DELL'OUTPUT
--   Il valore NULL è reso come «∅» (`\pset null`).
--   Ogni criterio produce una tabella in forma lunga:
--       criterio | controllo | valore | atteso | esito
--   `esito` vale PASS, «*** FAIL ***» oppure «info» (riga di sola informazione, senza soglia).
--   L'ultima riga di ogni blocco è l'ESITO COMPLESSIVO del criterio. Il gate è verde solo se
--   TUTTI i criteri sono PASS (§3: «Il gate è verde solo se tutti i criteri passano»).
--   Dove serve, dopo il criterio c'è una query di dettaglio che elenca le righe colpevoli
--   (limitata a 50 righe: serve a diagnosticare, non a fare un dump).
--
-- CRITERI NON VERIFICABILI IN SQL (dichiarati, non simulati)
--   C8  — è un criterio sulla DASHBOARD, che vive fuori da questo monorepo (censimento in
--         ADR-0039). Qui c'è solo una query di SUPPORTO sui dati sottostanti; l'esito di C8
--         resta N/A e va prodotto per ispezione visiva. Vedi il blocco C8.
--   C3  — la verifica del SEGNO «a campione contro l'export HL» richiede il CSV di Hyperliquid
--         (in ora locale CEST) e non è replicabile in SQL. Qui si verifica la sola PRESENZA
--         delle righe; il resto è materiale per il confronto manuale. Vedi il blocco C3.
--   C9  — la seconda parte («se l'indagine `reasoning_tokens=0` conferma la sottostima») è
--         un'indagine sul provider, non una query. Vedi il blocco C9.
--
-- FONTI
--   Criteri: docs/M6.2-PLAN.md §3 · Esito r2: §7 + docs/NOTA-METODOLOGICA-M6.2-R2.md
--   Schema:  src/aiat/db/models/*.py · alembic/versions/001_initial_schema.py (+002/003/004)
--   C7:      docs/decisions/0034-failure-stage-vocabulary.md (vocabolario chiuso)
--   Scoping: docs/decisions/0039-experiment-scoped-open-position-lookups.md
--   C5:      docs/decisions/0033-tax-sim-writer.md (periodo daily, rate 0.33)
--   C3:      docs/decisions/0031-funding-ledger.md (segno PRD §3.2.6, + = pagato)
--   C2:      docs/decisions/0032-autonomous-close-fee.md (fee di liquidazione deferita)
-- =============================================================================================

\set ON_ERROR_STOP on

\if :{?experiment_id}
\else
\echo '*** ERRORE: parametro obbligatorio mancante.'
\echo '    Uso: psql "$AIAT_DATABASE_URL" -v experiment_id=<uuid> [-v window_start=... -v window_end=...] -f scripts/gate_check.sql'
\quit
\endif

\if :{?window_start}
\else
\set window_start '-infinity'
\endif

\if :{?window_end}
\else
\set window_end 'infinity'
\endif

-- Tutti i timestamp del DB sono UTC (l'avvertenza di C3 sul CSV HL in CEST riguarda il file,
-- non il DB). date_trunc/::date su timestamptz dipendono dal TimeZone di sessione: lo fissiamo.
SET TIME ZONE 'UTC';

\pset null '∅'
\pset footer off

\echo ''
\echo '#############################################################################'
\echo '# GATE M6.2 — batteria C1-C9 (docs/M6.2-PLAN.md §3) — READ ONLY'
\echo '#############################################################################'
\echo '# experiment_id :' :'experiment_id'
\echo '# window_start  :' :'window_start'
\echo '# window_end    :' :'window_end'
\echo ''


-- =============================================================================================
-- C0 — CONTESTO (non è un criterio). Identifica l'esperimento e la finestra effettiva, così
--      l'evidenza incollata in M6.2-PLAN.md §7 è auto-descrittiva e riproducibile.
--      Il `git_commit_sha` unico su tutti i run è la precondizione P6 del piano (§2).
-- =============================================================================================
\echo '--- C0 — contesto esperimento (non è un criterio) ---------------------------'
SELECT
    'C0'                                                       AS criterio,
    e.name                                                     AS esperimento,
    e.started_at,
    e.ended_at,
    e.git_commit_sha                                           AS sha_esperimento,
    (SELECT count(DISTINCT r.model_id) FROM runs r
      WHERE r.experiment_id = :'experiment_id'::uuid)          AS modelli_con_run,
    (SELECT count(DISTINCT r.git_commit_sha) FROM runs r
      WHERE r.experiment_id = :'experiment_id'::uuid
        AND r.scheduled_for >= :'window_start'::timestamptz
        AND r.scheduled_for <  :'window_end'::timestamptz)     AS sha_distinti_nei_run_P6,
    (SELECT min(r.scheduled_for) FROM runs r
      WHERE r.experiment_id = :'experiment_id'::uuid
        AND r.scheduled_for >= :'window_start'::timestamptz
        AND r.scheduled_for <  :'window_end'::timestamptz)     AS primo_tick_in_finestra,
    (SELECT max(r.scheduled_for) FROM runs r
      WHERE r.experiment_id = :'experiment_id'::uuid
        AND r.scheduled_for >= :'window_start'::timestamptz
        AND r.scheduled_for <  :'window_end'::timestamptz)     AS ultimo_tick_in_finestra
FROM experiments e
WHERE e.id = :'experiment_id'::uuid;


-- =============================================================================================
-- C1 — AFFIDABILITÀ RUN
--   Testo (§3): «≥95% dei run in `success` per ogni modello (non solo aggregato). Gli errori di
--   schema-compliance di GPT 4.1 mini contano come fallimenti: se il modello resta sotto soglia
--   per questa causa, la decisione (accettare come dato comportamentale con soglia dedicata, o
--   mitigare con retry) va presa ed esplicitata in ADR prima di M7.»
--
--   COME SI LEGGE: una riga per modello. PASS se `pct_success` ≥ 95,00 per OGNI modello; il
--   criterio è esplicitamente per-modello, quindi un aggregato verde con un modello rosso è FAIL.
--   Finestra su `runs.scheduled_for` (il tick pianificato, non l'ora di completamento).
--   `pct_success_o_partial` è INFORMATIVA: `partial` (ADR-0024, un'azione non eseguita su un
--   loop completato) NON è `success` e non entra nella soglia — il criterio dice `success`.
--   Nota: il denominatore sono le righe `runs` realmente persistite. Un tick "mancato" non
--   crea alcuna riga (`error_kind='MissedTick'`, ADR-0034), quindi non compare qui: la
--   copertura dei tick è misurata dalla query C1-cop più sotto.
--   Nota sulla seconda parte del criterio: il fallimento per schema-compliance è distinguibile
--   guardando `failure_stage='llm_parse'` nella query di dettaglio C1-det; la DECISIONE che il
--   criterio richiede (soglia dedicata vs retry) è un atto documentale — un ADR — non una query.
-- =============================================================================================
\echo ''
\echo '--- C1 — affidabilità run (≥95% success per ogni modello) --------------------'
WITH r AS (
    SELECT model_id, status
    FROM runs
    WHERE experiment_id = :'experiment_id'::uuid
      AND scheduled_for >= :'window_start'::timestamptz
      AND scheduled_for <  :'window_end'::timestamptz
)
SELECT
    'C1'                                                                  AS criterio,
    model_id                                                              AS modello,
    count(*)                                                              AS n_run,
    count(*) FILTER (WHERE status = 'success')                            AS n_success,
    count(*) FILTER (WHERE status = 'partial')                            AS n_partial,
    count(*) FILTER (WHERE status <> 'success')                           AS n_non_success,
    round(100.0 * count(*) FILTER (WHERE status = 'success')
          / nullif(count(*), 0), 2)                                       AS pct_success,
    round(100.0 * count(*) FILTER (WHERE status IN ('success','partial'))
          / nullif(count(*), 0), 2)                                       AS pct_success_o_partial,
    CASE
        WHEN count(*) = 0 THEN '*** FAIL *** (nessun run)'
        WHEN 100.0 * count(*) FILTER (WHERE status = 'success') / count(*) >= 95 THEN 'PASS'
        ELSE '*** FAIL ***'
    END                                                                   AS esito
FROM r
GROUP BY model_id
ORDER BY model_id;

-- C1-det — dettaglio dei run non-success: perché hanno fallito (asse `failure_stage`,
-- vocabolario chiuso di ADR-0034). `llm_parse` = schema-compliance, la causa nominata dal criterio.
\echo '--- C1-det — ripartizione dei run non-success per stato e failure_stage ------'
SELECT
    'C1-det'                                       AS criterio,
    model_id                                       AS modello,
    status                                         AS stato_run,
    coalesce(failure_stage, '(nullo)')             AS failure_stage,
    count(*)                                       AS n_run
FROM runs
WHERE experiment_id = :'experiment_id'::uuid
  AND scheduled_for >= :'window_start'::timestamptz
  AND scheduled_for <  :'window_end'::timestamptz
  AND status <> 'success'
GROUP BY model_id, status, failure_stage
ORDER BY model_id, n_run DESC
LIMIT 50;

-- C1-cop — copertura dei tick (INFORMATIVA, non è C1): quanti slot da 15 minuti fra il primo e
-- l'ultimo run osservato NON hanno prodotto alcuna riga `runs`. Sono i tick mancati, che il
-- denominatore di C1 non può vedere (non esiste la riga). Lo scheduler gira a 0/15/30/45
-- (`orchestration/scheduler.py`), quindi uno slot = 900 s.
\echo '--- C1-cop — copertura tick: slot da 15 min senza riga runs (informativa) ----'
WITH r AS (
    SELECT model_id, scheduled_for
    FROM runs
    WHERE experiment_id = :'experiment_id'::uuid
      AND scheduled_for >= :'window_start'::timestamptz
      AND scheduled_for <  :'window_end'::timestamptz
)
SELECT
    'C1-cop'                                                                     AS criterio,
    model_id                                                                     AS modello,
    count(DISTINCT scheduled_for)                                                AS slot_con_run,
    (extract(epoch FROM (max(scheduled_for) - min(scheduled_for))) / 900)::int + 1
                                                                                 AS slot_attesi,
    (extract(epoch FROM (max(scheduled_for) - min(scheduled_for))) / 900)::int + 1
        - count(DISTINCT scheduled_for)                                          AS slot_senza_run,
    'info'                                                                       AS esito
FROM r
GROUP BY model_id
ORDER BY model_id;


-- =============================================================================================
-- C2 — LEDGER FEE COMPLETO
--   Testo (§3): «Ogni chiusura ha la sua riga `fee_events` `taker_close` con `fee_usd > 0`;
--   ogni apertura ha `taker_open`. N chiusure = N righe close.»
--
--   COME SI LEGGE: PASS quando i tre controlli con `atteso = 0` valgono 0. Finestra su
--   `positions.opened_at` per il lato apertura e su `positions.closed_at` per il lato chiusura
--   (una posizione aperta in finestra e chiusa fuori conta solo come apertura, ed è corretto).
--   Nota strutturale (`db/repositories/positions.py::_fee_type`): la fee di apertura è sempre
--   `taker_open`, quella di chiusura sempre `taker_close`, anche per una chiusura autonoma
--   SL/TP (ADR-0032). Le righe `maker_*` sono ammesse dal CHECK ma nessun path le scrive:
--   sono riportate come informazione, un valore ≠ 0 è un fatto nuovo da spiegare.
--   ECCEZIONE NOTA E ACCETTATA: la fee di LIQUIDAZIONE non è modellata (ADR-0032, deferita;
--   `M6.2-PLAN.md` §5). Una posizione con `close_reason='liquidated'` fa legittimamente FAIL
--   su questo criterio: la query di dettaglio C2-det mostra `close_reason`, così la distinzione
--   fra "bug del ledger" e "deferral dichiarato" si legge a occhio invece di essere assunta.
-- =============================================================================================
\echo ''
\echo '--- C2 — ledger fee completo ------------------------------------------------'
WITH aperte AS (
    SELECT p.id
    FROM positions p
    WHERE p.experiment_id = :'experiment_id'::uuid
      AND p.opened_at >= :'window_start'::timestamptz
      AND p.opened_at <  :'window_end'::timestamptz
), chiuse AS (
    SELECT p.id
    FROM positions p
    WHERE p.experiment_id = :'experiment_id'::uuid
      AND p.closed_at IS NOT NULL
      AND p.closed_at >= :'window_start'::timestamptz
      AND p.closed_at <  :'window_end'::timestamptz
), fee AS (
    SELECT position_id,
           count(*) FILTER (WHERE fee_type = 'taker_open')                       AS n_open,
           count(*) FILTER (WHERE fee_type = 'taker_open'  AND fee_usd > 0)      AS n_open_pos,
           count(*) FILTER (WHERE fee_type = 'taker_close')                      AS n_close,
           count(*) FILTER (WHERE fee_type = 'taker_close' AND fee_usd > 0)      AS n_close_pos,
           count(*) FILTER (WHERE fee_type IN ('maker_open','maker_close'))      AS n_maker
    FROM fee_events
    WHERE experiment_id = :'experiment_id'::uuid
    GROUP BY position_id
), chk(ord, controllo, valore, atteso) AS (
    VALUES
      (1, 'aperture in finestra',
          (SELECT count(*) FROM aperte)::numeric, NULL::numeric),
      (2, 'chiusure in finestra',
          (SELECT count(*) FROM chiuse)::numeric, NULL::numeric),
      (3, 'aperture SENZA riga fee taker_open',
          (SELECT count(*) FROM aperte a LEFT JOIN fee f ON f.position_id = a.id
            WHERE coalesce(f.n_open, 0) = 0)::numeric, 0::numeric),
      (4, 'chiusure SENZA riga fee taker_close con fee_usd > 0',
          (SELECT count(*) FROM chiuse c LEFT JOIN fee f ON f.position_id = c.id
            WHERE coalesce(f.n_close_pos, 0) = 0)::numeric, 0::numeric),
      (5, 'differenza  (n. chiusure) − (n. righe fee taker_close)',
          ((SELECT count(*) FROM chiuse)
           - (SELECT coalesce(sum(f.n_close), 0) FROM chiuse c JOIN fee f ON f.position_id = c.id)
          )::numeric, 0::numeric),
      (6, 'chiusure con PIÙ di una riga taker_close',
          (SELECT count(*) FROM chiuse c JOIN fee f ON f.position_id = c.id
            WHERE f.n_close > 1)::numeric, 0::numeric),
      (7, 'aperture con fee taker_open = 0 (presente ma nulla)',
          (SELECT count(*) FROM aperte a JOIN fee f ON f.position_id = a.id
            WHERE f.n_open > 0 AND f.n_open_pos = 0)::numeric, NULL::numeric),
      (8, 'righe fee maker_* (nessun path le scrive)',
          (SELECT coalesce(sum(f.n_maker), 0) FROM fee f)::numeric, NULL::numeric)
)
SELECT criterio, controllo, valore, atteso, esito FROM (
    SELECT ord, 'C2' AS criterio, controllo, valore, atteso,
           CASE WHEN atteso IS NULL THEN 'info'
                WHEN valore = atteso THEN 'PASS'
                ELSE '*** FAIL ***' END AS esito
    FROM chk
    UNION ALL
    SELECT 99, 'C2', 'ESITO COMPLESSIVO', NULL, NULL,
           CASE WHEN EXISTS (SELECT 1 FROM chk WHERE atteso IS NOT NULL AND valore <> atteso)
                THEN '*** FAIL ***' ELSE 'PASS' END
) x ORDER BY x.ord;

-- C2-det — le posizioni colpevoli. `close_reason` distingue il deferral dichiarato
-- (`liquidated`, ADR-0032) da un vero buco del ledger.
\echo '--- C2-det — posizioni con fee mancanti (max 50) -----------------------------'
SELECT
    'C2-det'                                                                  AS criterio,
    p.model_id                                                                AS modello,
    p.id                                                                      AS position_id,
    p.symbol,
    p.opened_at,
    p.closed_at,
    coalesce(p.close_reason, '(aperta)')                                      AS close_reason,
    count(fe.id) FILTER (WHERE fe.fee_type = 'taker_open')                    AS n_taker_open,
    count(fe.id) FILTER (WHERE fe.fee_type = 'taker_close')                   AS n_taker_close,
    coalesce(sum(fe.fee_usd) FILTER (WHERE fe.fee_type = 'taker_close'), 0)   AS fee_close_usd
FROM positions p
LEFT JOIN fee_events fe
       ON fe.position_id = p.id
      AND fe.experiment_id = :'experiment_id'::uuid
WHERE p.experiment_id = :'experiment_id'::uuid
  AND (   (p.opened_at >= :'window_start'::timestamptz AND p.opened_at < :'window_end'::timestamptz)
       OR (p.closed_at >= :'window_start'::timestamptz AND p.closed_at < :'window_end'::timestamptz))
GROUP BY p.id, p.model_id, p.symbol, p.opened_at, p.closed_at, p.close_reason
HAVING count(fe.id) FILTER (WHERE fe.fee_type = 'taker_open') = 0
    OR (p.closed_at IS NOT NULL
        AND count(fe.id) FILTER (WHERE fe.fee_type = 'taker_close' AND fe.fee_usd > 0) = 0)
ORDER BY p.opened_at
LIMIT 50;


-- =============================================================================================
-- C3 — LEDGER FUNDING CORRETTO
--   Testo (§3): «Righe `funding_events` presenti per ogni posizione con holding > 8h, segno in
--   convenzione PRD §3.2.6 (positivo = pagato), verificato a campione contro l'export HL
--   (attenzione: CSV in ora locale CEST, DB in UTC).»
--
--   VERIFICABILE IN SQL: solo la PRIMA metà — la PRESENZA delle righe per le posizioni con
--   holding > 8h. PASS quando `posizioni con holding > 8h SENZA funding_events` = 0.
--   NON VERIFICABILE IN SQL: la seconda metà. «Verificato a campione contro l'export HL» è per
--   definizione un confronto con un file esterno (il CSV `funding-history` di Hyperliquid, in
--   ora locale CEST) che il DB non contiene: nessuna query può stabilire se il segno memorizzato
--   corrisponde al pagamento reale. Si verifica così: si scarica il CSV per il wallet del
--   modello (l'indirizzo è in `models.wallet_address`), si convertono i suoi timestamp da CEST a
--   UTC (−2h in estate) e si confronta riga per riga con l'output di C3-hl qui sotto, ricordando
--   la convenzione: HL usa `usdc` con `+ = incassato`, il DB usa `funding_amount_usd` con
--   `+ = pagato` (PRD §3.2.6) — il reconciler NEGA al momento dell'ingest (ADR-0031, sezione
--   «Convenzione di segno del funding»). Quindi il match atteso è `funding_amount_usd = −usdc`.
--   LIMITE STRUTTURALE DA TENERE PRESENTE: il `FundingReconciler` scrive solo contro posizioni
--   APERTE al momento in cui gira (ogni 8h, `hour='0,8,16'`). Una posizione aperta e chiusa
--   fra due passaggi può non avere righe: per questo il criterio è formulato su holding > 8h.
--   Le posizioni ancora aperte sono valutate con `now()` come chiusura convenzionale.
-- =============================================================================================
\echo ''
\echo '--- C3 — ledger funding: presenza righe (la parte SQL-verificabile) ----------'
WITH pos AS (
    SELECT p.id, p.model_id, p.symbol, p.side, p.opened_at,
           coalesce(p.closed_at, now()) AS chiusura_effettiva
    FROM positions p
    WHERE p.experiment_id = :'experiment_id'::uuid
      AND p.opened_at >= :'window_start'::timestamptz
      AND p.opened_at <  :'window_end'::timestamptz
), lunghe AS (
    SELECT * FROM pos WHERE chiusura_effettiva - opened_at > interval '8 hours'
), fund AS (
    SELECT position_id, count(*) AS n, sum(funding_amount_usd) AS tot
    FROM funding_events
    WHERE experiment_id = :'experiment_id'::uuid
    GROUP BY position_id
), chk(ord, controllo, valore, atteso) AS (
    VALUES
      (1, 'posizioni in finestra',
          (SELECT count(*) FROM pos)::numeric, NULL::numeric),
      (2, 'posizioni con holding > 8h',
          (SELECT count(*) FROM lunghe)::numeric, NULL::numeric),
      (3, 'posizioni con holding > 8h SENZA righe funding_events',
          (SELECT count(*) FROM lunghe l LEFT JOIN fund f ON f.position_id = l.id
            WHERE coalesce(f.n, 0) = 0)::numeric, 0::numeric),
      (4, 'righe funding_events dell''esperimento',
          (SELECT count(*) FROM funding_events
            WHERE experiment_id = :'experiment_id'::uuid)::numeric, NULL::numeric),
      (5, 'righe funding_events orfane (posizione di altro esperimento)',
          (SELECT count(*) FROM funding_events fe
             LEFT JOIN positions p ON p.id = fe.position_id
                                  AND p.experiment_id = :'experiment_id'::uuid
            WHERE fe.experiment_id = :'experiment_id'::uuid
              AND p.id IS NULL)::numeric, 0::numeric),
      (6, 'righe con periodo != 1h (il writer scrive end − 1h)',
          (SELECT count(*) FROM funding_events
            WHERE experiment_id = :'experiment_id'::uuid
              AND funding_period_end - funding_period_start <> interval '1 hour')::numeric,
          NULL::numeric),
      (7, 'duplicati sulla chiave naturale (position_id, funding_period_end)',
          (SELECT coalesce(sum(n - 1), 0) FROM (
              SELECT count(*) AS n FROM funding_events
               WHERE experiment_id = :'experiment_id'::uuid
               GROUP BY position_id, funding_period_end HAVING count(*) > 1) d)::numeric,
          0::numeric),
      (8, 'somma funding_amount_usd (convenzione PRD: + = pagato)',
          (SELECT coalesce(sum(funding_amount_usd), 0) FROM funding_events
            WHERE experiment_id = :'experiment_id'::uuid)::numeric, NULL::numeric)
)
SELECT criterio, controllo, valore, atteso, esito FROM (
    SELECT ord, 'C3' AS criterio, controllo, valore, atteso,
           CASE WHEN atteso IS NULL THEN 'info'
                WHEN valore = atteso THEN 'PASS'
                ELSE '*** FAIL ***' END AS esito
    FROM chk
    UNION ALL
    SELECT 99, 'C3', 'ESITO COMPLESSIVO (solo parte SQL: presenza righe)', NULL, NULL,
           CASE WHEN EXISTS (SELECT 1 FROM chk WHERE atteso IS NOT NULL AND valore <> atteso)
                THEN '*** FAIL ***' ELSE 'PASS' END
) x ORDER BY x.ord;

-- C3-det — le posizioni con holding > 8h senza alcuna riga funding.
\echo '--- C3-det — posizioni holding > 8h senza funding_events (max 50) -----------'
SELECT
    'C3-det'                                                            AS criterio,
    p.model_id                                                          AS modello,
    p.id                                                                AS position_id,
    p.symbol,
    p.side,
    p.opened_at,
    p.closed_at,
    round(extract(epoch FROM (coalesce(p.closed_at, now()) - p.opened_at)) / 3600.0, 2)
                                                                        AS holding_ore
FROM positions p
WHERE p.experiment_id = :'experiment_id'::uuid
  AND p.opened_at >= :'window_start'::timestamptz
  AND p.opened_at <  :'window_end'::timestamptz
  AND coalesce(p.closed_at, now()) - p.opened_at > interval '8 hours'
  AND NOT EXISTS (SELECT 1 FROM funding_events fe WHERE fe.position_id = p.id)
ORDER BY p.opened_at
LIMIT 50;

-- C3-seg — INDIZIO di coerenza interna del segno, NON la verifica del criterio.
-- Meccanica dei perp: con `funding_rate > 0` pagano i LONG e incassano gli SHORT (e viceversa).
-- Nella convenzione PRD §3.2.6 (+ = pagato) ci si aspetta quindi, riga per riga:
--     LONG  → sign(funding_amount_usd) =  sign(funding_rate)
--     SHORT → sign(funding_amount_usd) = −sign(funding_rate)
-- Le righe che violano questa attesa sono candidate a un'inversione di segno; ma la PROVA
-- resta il confronto con il CSV HL (C3-hl). Righe con rate = 0 sono escluse.
\echo '--- C3-seg — coerenza interna rate↔importo (indizio, NON la verifica) --------'
WITH fe AS (
    SELECT f.id, f.model_id, p.side, f.funding_rate, f.funding_amount_usd
    FROM funding_events f
    JOIN positions p ON p.id = f.position_id
    WHERE f.experiment_id = :'experiment_id'::uuid
      AND f.funding_rate <> 0
      AND f.funding_amount_usd <> 0
)
SELECT
    'C3-seg'                                                  AS criterio,
    model_id                                                  AS modello,
    side                                                      AS lato,
    count(*)                                                  AS n_righe,
    count(*) FILTER (
        WHERE (side = 'LONG'  AND sign(funding_amount_usd) <> sign(funding_rate))
           OR (side = 'SHORT' AND sign(funding_amount_usd) <>  -sign(funding_rate))
    )                                                         AS righe_con_segno_inatteso,
    'indizio'                                                 AS esito
FROM fe
GROUP BY model_id, side
ORDER BY model_id, side;

-- C3-hl — materiale per il confronto MANUALE con l'export HL (§3: «verificato a campione»).
-- Aggregato per modello e giorno UTC. `atteso_nel_csv_hl_usdc` è il valore con cui confrontare
-- la colonna `usdc` del CSV, cioè l'importo DB con il segno rovesciato (ADR-0031).
-- ATTENZIONE: il CSV HL è in ora locale CEST; sottrarre 2h (1h in ora solare) prima del confronto.
\echo '--- C3-hl — funding per modello e giorno UTC (per il confronto col CSV HL) ---'
SELECT
    'C3-hl'                                        AS criterio,
    f.model_id                                     AS modello,
    date_trunc('day', f.funding_period_end)::date  AS giorno_utc,
    count(*)                                       AS n_pagamenti,
    sum(f.funding_amount_usd)                      AS somma_db_usd_piu_uguale_pagato,
    -sum(f.funding_amount_usd)                     AS atteso_nel_csv_hl_usdc,
    'info'                                         AS esito
FROM funding_events f
WHERE f.experiment_id = :'experiment_id'::uuid
GROUP BY f.model_id, date_trunc('day', f.funding_period_end)::date
ORDER BY modello, giorno_utc
LIMIT 50;


-- =============================================================================================
-- C4 — OUTCOMES COMPLETI
--   Testo (§3): «N posizioni chiuse = N outcomes; `sum_fees_usd > 0` su tutti; campi derivati
--   coerenti (spot check: net = gross − fee − funding).»
--
--   COME SI LEGGE: PASS quando tutti i controlli con `atteso = 0` valgono 0. Finestra su
--   `positions.closed_at`. Lo «spot check» del criterio qui è fatto per intero e non a
--   campione: la formula è deterministica (`execution/outcome_resolver.py:119-123` e
--   `db/repositories/positions.py`), quindi la si verifica su TUTTE le righe —
--       pnl_net_fee_usd          = realized_pnl_gross_usd − sum_fees_usd
--       pnl_net_fee_funding_usd  = pnl_net_fee_usd − sum_funding_usd   (funding + = pagato)
--   più le due derivate booleane (`was_profitable_net`, `horizon_met`) e la coerenza fra
--   `outcomes.realized_pnl_gross_usd` e `positions.realized_pnl_usd`.
--   `pnl_net_fee_funding_tax_sim_usd` è per costruzione 0 su ogni riga (mai popolato dal
--   resolver né dal close path — ADR-0014): è riportato come informazione, non come soglia.
--   ECCEZIONE NOTA: `sum_fees_usd > 0` non vale per una chiusura per liquidazione, la cui fee
--   non è modellata (ADR-0032) — vedi la nota di C2.
-- =============================================================================================
\echo ''
\echo '--- C4 — outcomes completi --------------------------------------------------'
WITH chiuse AS (
    SELECT p.id, p.model_id, p.realized_pnl_usd
    FROM positions p
    WHERE p.experiment_id = :'experiment_id'::uuid
      AND p.closed_at IS NOT NULL
      AND p.closed_at >= :'window_start'::timestamptz
      AND p.closed_at <  :'window_end'::timestamptz
), coppie AS (
    -- colonne esplicite: `o.*` collide con `position_id` di `chiuse` (riferimento ambiguo)
    SELECT o.id                              AS outcome_id,
           o.position_id                     AS position_id,
           c.realized_pnl_usd                AS pos_realized_pnl_usd,
           o.realized_pnl_gross_usd,
           o.sum_fees_usd,
           o.sum_funding_usd,
           o.pnl_net_fee_usd,
           o.pnl_net_fee_funding_usd,
           o.pnl_net_fee_funding_tax_sim_usd,
           o.was_profitable_net,
           o.holding_duration_min,
           o.decision_action_time_horizon_min,
           o.horizon_met
    FROM chiuse c
    JOIN outcomes o ON o.position_id = c.id
), chk(ord, controllo, valore, atteso) AS (
    VALUES
      (1, 'posizioni chiuse in finestra',
          (SELECT count(*) FROM chiuse)::numeric, NULL::numeric),
      (2, 'outcomes abbinati',
          (SELECT count(*) FROM coppie)::numeric, NULL::numeric),
      (3, 'differenza  (n. chiusure) − (n. outcomes)',
          ((SELECT count(*) FROM chiuse) - (SELECT count(*) FROM coppie))::numeric, 0::numeric),
      (4, 'outcomes orfani (posizione di altro esperimento o non chiusa)',
          (SELECT count(*) FROM outcomes o
             LEFT JOIN positions p ON p.id = o.position_id
            WHERE o.experiment_id = :'experiment_id'::uuid
              AND (p.id IS NULL
                   OR p.experiment_id <> :'experiment_id'::uuid
                   OR p.closed_at IS NULL))::numeric, 0::numeric),
      (5, 'outcomes con sum_fees_usd <= 0',
          (SELECT count(*) FROM coppie WHERE sum_fees_usd <= 0)::numeric, 0::numeric),
      (6, 'derivato incoerente: pnl_net_fee <> gross − fee',
          (SELECT count(*) FROM coppie
            WHERE pnl_net_fee_usd <> realized_pnl_gross_usd - sum_fees_usd)::numeric, 0::numeric),
      (7, 'derivato incoerente: pnl_net_fee_funding <> pnl_net_fee − funding',
          (SELECT count(*) FROM coppie
            WHERE pnl_net_fee_funding_usd <> pnl_net_fee_usd - sum_funding_usd)::numeric,
          0::numeric),
      (8, 'derivato incoerente: was_profitable_net <> (pnl_net_fee_funding > 0)',
          (SELECT count(*) FROM coppie
            WHERE was_profitable_net <> (pnl_net_fee_funding_usd > 0))::numeric, 0::numeric),
      (9, 'derivato incoerente: horizon_met <> (holding <= time_horizon)',
          (SELECT count(*) FROM coppie
            WHERE horizon_met <> (holding_duration_min <= decision_action_time_horizon_min)
          )::numeric, 0::numeric),
     (10, 'outcome.gross <> position.realized_pnl_usd',
          (SELECT count(*) FROM coppie
            WHERE realized_pnl_gross_usd <> pos_realized_pnl_usd)::numeric, 0::numeric),
     (11, 'outcome.sum_fees_usd <> somma fee_events della posizione',
          (SELECT count(*) FROM coppie c
            WHERE c.sum_fees_usd <> (SELECT coalesce(sum(fe.fee_usd), 0)
                                       FROM fee_events fe
                                      WHERE fe.position_id = c.position_id))::numeric, 0::numeric),
     (12, 'outcome.sum_funding_usd <> somma funding_events della posizione',
          (SELECT count(*) FROM coppie c
            WHERE c.sum_funding_usd <> (SELECT coalesce(sum(fu.funding_amount_usd), 0)
                                          FROM funding_events fu
                                         WHERE fu.position_id = c.position_id))::numeric,
          0::numeric),
     (13, 'outcomes con pnl_net_fee_funding_tax_sim_usd <> 0 (atteso 0 per ADR-0014)',
          (SELECT count(*) FROM coppie
            WHERE pnl_net_fee_funding_tax_sim_usd <> 0)::numeric, NULL::numeric)
)
SELECT criterio, controllo, valore, atteso, esito FROM (
    SELECT ord, 'C4' AS criterio, controllo, valore, atteso,
           CASE WHEN atteso IS NULL THEN 'info'
                WHEN valore = atteso THEN 'PASS'
                ELSE '*** FAIL ***' END AS esito
    FROM chk
    UNION ALL
    SELECT 99, 'C4', 'ESITO COMPLESSIVO', NULL, NULL,
           CASE WHEN EXISTS (SELECT 1 FROM chk WHERE atteso IS NOT NULL AND valore <> atteso)
                THEN '*** FAIL ***' ELSE 'PASS' END
) x ORDER BY x.ord;

-- C4-det — le righe colpevoli, con la ragione.
\echo '--- C4-det — outcomes/chiusure incoerenti (max 50) ---------------------------'
SELECT
    'C4-det'                                                        AS criterio,
    p.model_id                                                      AS modello,
    p.id                                                            AS position_id,
    p.symbol,
    p.closed_at,
    coalesce(p.close_reason, '(aperta)')                            AS close_reason,
    o.id                                                            AS outcome_id,
    o.realized_pnl_gross_usd,
    o.sum_fees_usd,
    o.sum_funding_usd,
    o.pnl_net_fee_usd,
    o.pnl_net_fee_funding_usd,
    CASE
        WHEN o.id IS NULL                                                  THEN 'chiusura senza outcome'
        WHEN o.sum_fees_usd <= 0                                           THEN 'sum_fees_usd <= 0'
        WHEN o.pnl_net_fee_usd <> o.realized_pnl_gross_usd - o.sum_fees_usd
                                                                           THEN 'net_fee incoerente'
        WHEN o.pnl_net_fee_funding_usd <> o.pnl_net_fee_usd - o.sum_funding_usd
                                                                           THEN 'net_fee_funding incoerente'
        WHEN o.realized_pnl_gross_usd <> p.realized_pnl_usd                THEN 'gross <> position'
        WHEN o.was_profitable_net <> (o.pnl_net_fee_funding_usd > 0)       THEN 'was_profitable_net incoerente'
        WHEN o.horizon_met <> (o.holding_duration_min <= o.decision_action_time_horizon_min)
                                                                           THEN 'horizon_met incoerente'
    END                                                             AS motivo
FROM positions p
LEFT JOIN outcomes o ON o.position_id = p.id
WHERE p.experiment_id = :'experiment_id'::uuid
  AND p.closed_at IS NOT NULL
  AND p.closed_at >= :'window_start'::timestamptz
  AND p.closed_at <  :'window_end'::timestamptz
  AND (   o.id IS NULL
       OR o.sum_fees_usd <= 0
       OR o.pnl_net_fee_usd <> o.realized_pnl_gross_usd - o.sum_fees_usd
       OR o.pnl_net_fee_funding_usd <> o.pnl_net_fee_usd - o.sum_funding_usd
       OR o.realized_pnl_gross_usd <> p.realized_pnl_usd
       OR o.was_profitable_net <> (o.pnl_net_fee_funding_usd > 0)
       OR o.horizon_met <> (o.holding_duration_min <= o.decision_action_time_horizon_min))
ORDER BY p.closed_at
LIMIT 50;


-- =============================================================================================
-- C5 — TAX SIM DAILY
--   Testo (§3): «1 riga per modello per giorno, label formato daily (no bug quarter), rate 0.33,
--   base netta coerente con gli outcomes del giorno.»
--
--   COME SI LEGGE: PASS quando tutti i controlli con `atteso = 0` valgono 0.
--   - «label formato daily» = `quarter_label` in formato `YYYY-MM-DD` (il job in modalità
--     `daily` etichetta il giorno UTC chiuso: `orchestration/tax_sim_runner.py:57,68`); una
--     label `Qn-YYYY` significa che il job gira in modalità `quarter` — è il «bug quarter».
--   - «rate 0.33» = `tax_rate_pct` (ADR-0033: override esplicito; il server_default dello
--     schema resta 0.26 e NON deve comparire qui).
--   - «base netta coerente con gli outcomes del giorno» = ricalcolo completo dagli `outcomes`
--     bucketati per `created_at` in `[period_start, period_end)` — è esattamente ciò che fa
--     il writer (`db/repositories/tax_simulation.py:57-64`):
--         taxable_base = max(0, Σgross − Σfee − Σfunding)   e   tax_due = base × rate
--   - «1 riga per modello per giorno»: la griglia attesa è (modelli con almeno un outcome
--     nell'esperimento) × (giorni fra il primo e l'ultimo tick osservato). DUE avvertenze
--     che vanno lette prima di dichiarare FAIL:
--       (a) il job scrive solo per i modelli che hanno ALMENO UN outcome nell'esperimento
--           (`tax_sim_runner._participating_models`): un modello che non ha mai chiuso nulla
--           non ha righe, ed è corretto;
--       (b) il job gira alle 00:05 UTC e calcola il giorno PRECEDENTE: l'ULTIMO giorno della
--           finestra ha una riga solo se il servizio era ancora attivo il giorno dopo.
--     Per questo la combinazione mancante è elencata in C5-det invece di essere solo contata.
--   TERZA AVVERTENZA, sul bucketing: il writer aggrega per `outcomes.created_at` — l'istante in
--   cui la riga è stata SCRITTA — non per `positions.closed_at` (`tax_sim_runner.py:16,164`).
--   Un outcome bookkeppato a posteriori dal `ClosureReconciler` (ADR-0038) finisce quindi nel
--   giorno in cui è stato riconciliato, non in quello della chiusura on-chain: è successo in r2,
--   dove 5 chiusure del 24-25/08 sono state scritte il 06/09 (nota r2 §5). Il ricalcolo di
--   C5-ric usa la stessa colonna del writer, quindi resta coerente; ma se un giorno "non torna",
--   questa è la prima cosa da controllare — e NON è un bug del ledger.
-- =============================================================================================
\echo ''
\echo '--- C5 — tax sim daily ------------------------------------------------------'
WITH tax AS (
    SELECT * FROM tax_sim_periods WHERE experiment_id = :'experiment_id'::uuid
), giorni AS (
    SELECT generate_series(
               date_trunc('day', min(scheduled_for)),
               date_trunc('day', max(scheduled_for)),
               interval '1 day')::date AS giorno
    FROM runs
    WHERE experiment_id = :'experiment_id'::uuid
      AND scheduled_for >= :'window_start'::timestamptz
      AND scheduled_for <  :'window_end'::timestamptz
), modelli AS (
    SELECT DISTINCT model_id FROM outcomes WHERE experiment_id = :'experiment_id'::uuid
), attesi AS (
    SELECT m.model_id, g.giorno FROM modelli m CROSS JOIN giorni g
), ricalcolo AS (
    SELECT t.id, t.model_id, t.quarter_label,
           t.total_pnl_gross_usd, t.total_fees_usd, t.total_funding_usd,
           t.taxable_base_usd, t.tax_rate_pct, t.tax_due_usd, t.n_positions_closed,
           coalesce(sum(o.realized_pnl_gross_usd), 0) AS ric_gross,
           coalesce(sum(o.sum_fees_usd), 0)           AS ric_fees,
           coalesce(sum(o.sum_funding_usd), 0)        AS ric_funding,
           count(o.id)                                AS ric_n
    FROM tax t
    LEFT JOIN outcomes o
           ON o.experiment_id = t.experiment_id
          AND o.model_id      = t.model_id
          AND o.created_at   >= t.period_start
          AND o.created_at   <  t.period_end
    GROUP BY t.id, t.model_id, t.quarter_label, t.total_pnl_gross_usd, t.total_fees_usd,
             t.total_funding_usd, t.taxable_base_usd, t.tax_rate_pct, t.tax_due_usd,
             t.n_positions_closed
), chk(ord, controllo, valore, atteso) AS (
    VALUES
      (1, 'righe tax_sim_periods dell''esperimento',
          (SELECT count(*) FROM tax)::numeric, NULL::numeric),
      (2, 'combinazioni (modello × giorno) attese',
          (SELECT count(*) FROM attesi)::numeric, NULL::numeric),
      (3, 'combinazioni (modello × giorno) SENZA riga  [leggere le avvertenze (a) e (b)]',
          (SELECT count(*) FROM attesi a
            WHERE NOT EXISTS (SELECT 1 FROM tax t
                               WHERE t.model_id = a.model_id
                                 AND t.quarter_label = to_char(a.giorno, 'YYYY-MM-DD')))::numeric,
          0::numeric),
      (4, 'label NON in formato daily YYYY-MM-DD (bug quarter)',
          (SELECT count(*) FROM tax
            WHERE quarter_label !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$')::numeric, 0::numeric),
      (5, 'label diversa dal giorno di period_start',
          (SELECT count(*) FROM tax
            WHERE quarter_label <> to_char(period_start, 'YYYY-MM-DD'))::numeric, 0::numeric),
      (6, 'periodi di durata <> 1 giorno',
          (SELECT count(*) FROM tax
            WHERE period_end - period_start <> interval '1 day')::numeric, 0::numeric),
      (7, 'righe con tax_rate_pct <> 0.33',
          (SELECT count(*) FROM tax WHERE tax_rate_pct <> 0.33)::numeric, 0::numeric),
      (8, 'righe con totali <> ricalcolo dagli outcomes del giorno',
          (SELECT count(*) FROM ricalcolo
            WHERE total_pnl_gross_usd <> ric_gross
               OR total_fees_usd      <> ric_fees
               OR total_funding_usd   <> ric_funding
               OR n_positions_closed  <> ric_n)::numeric, 0::numeric),
      (9, 'righe con taxable_base <> max(0, gross − fee − funding)',
          (SELECT count(*) FROM ricalcolo
            WHERE taxable_base_usd <> greatest(0, ric_gross - ric_fees - ric_funding))::numeric,
          0::numeric),
     (10, 'righe con tax_due <> taxable_base × rate (tolleranza 1e-8, arrot. Numeric(20,8))',
          (SELECT count(*) FROM ricalcolo
            WHERE abs(tax_due_usd - round(taxable_base_usd * tax_rate_pct, 8)) > 0.00000001
          )::numeric, 0::numeric),
     (11, 'duplicati (modello, label)  [la UNIQUE lo impedisce: 0 conferma il vincolo]',
          (SELECT coalesce(sum(n - 1), 0) FROM (
              SELECT count(*) AS n FROM tax GROUP BY model_id, quarter_label HAVING count(*) > 1
          ) d)::numeric, 0::numeric)
)
SELECT criterio, controllo, valore, atteso, esito FROM (
    SELECT ord, 'C5' AS criterio, controllo, valore, atteso,
           CASE WHEN atteso IS NULL THEN 'info'
                WHEN valore = atteso THEN 'PASS'
                ELSE '*** FAIL ***' END AS esito
    FROM chk
    UNION ALL
    SELECT 99, 'C5', 'ESITO COMPLESSIVO', NULL, NULL,
           CASE WHEN EXISTS (SELECT 1 FROM chk WHERE atteso IS NOT NULL AND valore <> atteso)
                THEN '*** FAIL ***' ELSE 'PASS' END
) x ORDER BY x.ord;

-- C5-det — le combinazioni (modello × giorno) senza riga. Prima di dichiarare FAIL, verificare
-- se il giorno è l'ULTIMO della finestra (riga attesa solo il giorno dopo, alle 00:05 UTC).
\echo '--- C5-det — (modello × giorno) senza riga tax_sim (max 50) ------------------'
WITH giorni AS (
    SELECT generate_series(
               date_trunc('day', min(scheduled_for)),
               date_trunc('day', max(scheduled_for)),
               interval '1 day')::date AS giorno
    FROM runs
    WHERE experiment_id = :'experiment_id'::uuid
      AND scheduled_for >= :'window_start'::timestamptz
      AND scheduled_for <  :'window_end'::timestamptz
), modelli AS (
    SELECT DISTINCT model_id FROM outcomes WHERE experiment_id = :'experiment_id'::uuid
)
SELECT
    'C5-det'                                              AS criterio,
    m.model_id                                            AS modello,
    g.giorno                                              AS giorno_mancante,
    (SELECT count(*) FROM outcomes o
      WHERE o.experiment_id = :'experiment_id'::uuid
        AND o.model_id = m.model_id
        AND o.created_at >= g.giorno::timestamptz
        AND o.created_at <  (g.giorno + 1)::timestamptz)  AS outcomes_quel_giorno,
    (g.giorno = (SELECT max(giorno) FROM giorni))         AS e_ultimo_giorno_finestra
FROM modelli m
CROSS JOIN giorni g
WHERE NOT EXISTS (
    SELECT 1 FROM tax_sim_periods t
     WHERE t.experiment_id = :'experiment_id'::uuid
       AND t.model_id = m.model_id
       AND t.quarter_label = to_char(g.giorno, 'YYYY-MM-DD'))
ORDER BY m.model_id, g.giorno
LIMIT 50;

-- C5-ric — ricalcolo riga per riga (evidenza da incollare per «base netta coerente»).
\echo '--- C5-ric — ricalcolo per riga tax_sim (max 50) ----------------------------'
SELECT
    'C5-ric'                                                       AS criterio,
    t.model_id                                                     AS modello,
    t.quarter_label                                                AS label,
    t.tax_rate_pct                                                 AS rate,
    t.n_positions_closed                                           AS n_chiusure_riga,
    count(o.id)                                                    AS n_chiusure_ricalcolo,
    t.taxable_base_usd                                             AS base_riga,
    greatest(0, coalesce(sum(o.realized_pnl_gross_usd), 0)
                - coalesce(sum(o.sum_fees_usd), 0)
                - coalesce(sum(o.sum_funding_usd), 0))             AS base_ricalcolo,
    t.tax_due_usd                                                  AS imposta_riga,
    round(greatest(0, coalesce(sum(o.realized_pnl_gross_usd), 0)
                      - coalesce(sum(o.sum_fees_usd), 0)
                      - coalesce(sum(o.sum_funding_usd), 0)) * t.tax_rate_pct, 8)
                                                                   AS imposta_ricalcolo
FROM tax_sim_periods t
LEFT JOIN outcomes o
       ON o.experiment_id = t.experiment_id
      AND o.model_id      = t.model_id
      AND o.created_at   >= t.period_start
      AND o.created_at   <  t.period_end
WHERE t.experiment_id = :'experiment_id'::uuid
GROUP BY t.id, t.model_id, t.quarter_label, t.tax_rate_pct, t.n_positions_closed,
         t.taxable_base_usd, t.tax_due_usd
ORDER BY t.model_id, t.quarter_label
LIMIT 50;


-- =============================================================================================
-- C6 — ZERO DIVERGENZE NON SPIEGATE
--   Testo (§3): «Nessun `ChainDivergence` nelle 48h. Qualsiasi occorrenza = gate rosso e
--   indagine (il fix root-cause T4b è bloccante pre-M7: se non ancora deployato allo smoke,
--   anche una sola zombie ripetuta blocca).»
--
--   COME SI LEGGE: PASS se `righe ChainDivergence in finestra` = 0. Il criterio è scritto in
--   forma ASSOLUTA («nessun», «qualsiasi occorrenza»), quindi qui non c'è tolleranza: 1 = FAIL.
--   Finestra su `errors.occurred_at`. Le righe `ChainDivergence` sono scritte da
--   `decision_loop._reconcile_chain_state` con `error_kind='ChainDivergence'` e il dettaglio
--   in `errors.context` (JSONB): NON sono fallimenti di run, sono osservazioni (ADR-0025).
--   Una riga può contenere PIÙ divergenze (tutte serializzate nello stesso `context`): la
--   colonna `n_divergenze_serializzate` le conta, così «una riga» non nasconde «68 segnalazioni».
--   La query di dettaglio C6-tot ignora la finestra ed è deliberata: serve a far emergere i
--   burst FUORI dalla finestra di gate (per r2: 15-22/08 su `cn-cheap`, 33/10/68/5, diagnosi
--   aperta — `M6.2-PLAN.md` §7.3 e nota r2 §6). Sono FAIL del criterio? No: C6 è valutato sulla
--   finestra. Vanno però letti prima di dichiarare chiuso il gate.
-- =============================================================================================
\echo ''
\echo '--- C6 — zero ChainDivergence nella finestra --------------------------------'
WITH cd AS (
    SELECT e.*,
           coalesce(jsonb_array_length(e.context -> 'divergences'), 0) AS n_div
    FROM errors e
    WHERE e.experiment_id = :'experiment_id'::uuid
      AND e.error_kind = 'ChainDivergence'
      AND e.occurred_at >= :'window_start'::timestamptz
      AND e.occurred_at <  :'window_end'::timestamptz
), chk(ord, controllo, valore, atteso) AS (
    VALUES
      (1, 'righe errors ChainDivergence in finestra',
          (SELECT count(*) FROM cd)::numeric, 0::numeric),
      (2, 'divergenze serializzate in quelle righe (context->divergences)',
          (SELECT coalesce(sum(n_div), 0) FROM cd)::numeric, 0::numeric),
      (3, 'modelli coinvolti',
          (SELECT count(DISTINCT model_id) FROM cd)::numeric, NULL::numeric),
      (4, 'posizioni aperte in DB a fine finestra (contesto della detection)',
          (SELECT count(*) FROM positions
            WHERE experiment_id = :'experiment_id'::uuid
              AND closed_at IS NULL)::numeric, NULL::numeric)
)
SELECT criterio, controllo, valore, atteso, esito FROM (
    SELECT ord, 'C6' AS criterio, controllo, valore, atteso,
           CASE WHEN atteso IS NULL THEN 'info'
                WHEN valore = atteso THEN 'PASS'
                ELSE '*** FAIL ***' END AS esito
    FROM chk
    UNION ALL
    SELECT 99, 'C6', 'ESITO COMPLESSIVO', NULL, NULL,
           CASE WHEN EXISTS (SELECT 1 FROM chk WHERE atteso IS NOT NULL AND valore <> atteso)
                THEN '*** FAIL ***' ELSE 'PASS' END
) x ORDER BY x.ord;

-- C6-tot — TUTTE le ChainDivergence dell'esperimento, per modello e giorno UTC (ignora la
-- finestra: serve a vedere i burst fuori dal gate). Per il TIPO di divergenza (zombie_row /
-- missing_row / size_mismatch, `orchestration/chain_reconciliation.py`) leggere `errors.context`
-- della singola riga: contiene `kind`, `position_id` e `delta` di ogni divergenza serializzata.
\echo '--- C6-tot — ChainDivergence su TUTTO l''esperimento, per giorno (max 50) ----'
SELECT
    'C6-tot'                                                       AS criterio,
    e.model_id                                                     AS modello,
    date_trunc('day', e.occurred_at)::date                         AS giorno_utc,
    count(*)                                                       AS n_righe,
    sum(coalesce(jsonb_array_length(e.context -> 'divergences'), 0)) AS n_divergenze,
    (e.occurred_at >= :'window_start'::timestamptz
     AND e.occurred_at < :'window_end'::timestamptz)               AS dentro_finestra_gate
FROM errors e
WHERE e.experiment_id = :'experiment_id'::uuid
  AND e.error_kind = 'ChainDivergence'
GROUP BY e.model_id, date_trunc('day', e.occurred_at)::date,
         (e.occurred_at >= :'window_start'::timestamptz
          AND e.occurred_at < :'window_end'::timestamptz)
ORDER BY giorno_utc, modello
LIMIT 50;


-- =============================================================================================
-- C7 — ERRORI CLASSIFICATI
--   Testo (§3): «Ogni run non-success ha riga `errors` con `error_kind` e `failure_stage`
--   valorizzati; nessun fallimento silenzioso (run failed senza errore corrispondente).»
--
--   COME SI LEGGE: PASS quando tutti i controlli con `atteso = 0` valgono 0.
--   PRECISAZIONE NECESSARIA, e dichiarata invece che nascosta: «run non-success» preso alla
--   lettera includerebbe anche `partial` e `running`, per i quali ADR-0034 prescrive
--   `failure_stage` NULL e nessuna riga `errors` (`partial` = loop completato con un'azione
--   fallita, ADR-0024; `running` = riga non finalizzata, es. processo ucciso). La glossa dello
--   stesso criterio — «run failed senza errore corrispondente» — dice quale sia l'insieme
--   operativo: `status IN ('failed','timeout')`. È su quello che si misura il criterio; gli
--   altri stati non-success sono contati a parte, come informazione da guardare.
--   `error_kind` = nome della classe di eccezione (`type(exc).__name__`, ADR-0034); il
--   `failure_stage` è vincolato al vocabolario CHIUSO {timeout, llm_auth, llm_rate, llm_parse,
--   error} — enforced in codice, NON da un CHECK sul DB (ADR-0034, «Alternative»): per questo
--   la query lo verifica esplicitamente.
--   `ChainDivergence` e `MissedTick` sono righe `errors` che NON sono fallimenti di run e sono
--   escluse dall'abbinamento (ADR-0034; ATLAS-2 §: «chi analizza errors deve filtrarle,
--   altrimenti sovrastima i fallimenti»).
--   Nota: un fallimento avvenuto PRIMA del commit di `create_run` non ha riga `runs` e lascia
--   una riga `errors` con `run_id` NULL (`decision_loop._record_failure`). Non è un fallimento
--   silenzioso — l'errore c'è — ma non è abbinabile: è contato a parte.
-- =============================================================================================
\echo ''
\echo '--- C7 — errori classificati ------------------------------------------------'
WITH r AS (
    SELECT * FROM runs
    WHERE experiment_id = :'experiment_id'::uuid
      AND scheduled_for >= :'window_start'::timestamptz
      AND scheduled_for <  :'window_end'::timestamptz
), falliti AS (
    SELECT * FROM r WHERE status IN ('failed', 'timeout')
), err AS (
    SELECT * FROM errors
    WHERE experiment_id = :'experiment_id'::uuid
      AND error_kind NOT IN ('ChainDivergence', 'MissedTick')
), chk(ord, controllo, valore, atteso) AS (
    VALUES
      (1, 'run in finestra',
          (SELECT count(*) FROM r)::numeric, NULL::numeric),
      (2, 'run failed/timeout (insieme su cui si misura il criterio)',
          (SELECT count(*) FROM falliti)::numeric, NULL::numeric),
      (3, 'run failed/timeout SENZA riga errors abbinata  [fallimento silenzioso]',
          (SELECT count(*) FROM falliti f
            WHERE NOT EXISTS (SELECT 1 FROM err e WHERE e.run_id = f.id))::numeric, 0::numeric),
      (4, 'run failed/timeout con failure_stage NULL',
          (SELECT count(*) FROM falliti WHERE failure_stage IS NULL)::numeric, 0::numeric),
      (5, 'run failed/timeout con failure_stage fuori dal vocabolario ADR-0034',
          (SELECT count(*) FROM falliti
            WHERE failure_stage IS NOT NULL
              AND failure_stage NOT IN ('timeout','llm_auth','llm_rate','llm_parse','error')
          )::numeric, 0::numeric),
      (6, 'righe errors abbinate con error_kind vuoto',
          (SELECT count(*) FROM err e JOIN falliti f ON f.id = e.run_id
            WHERE btrim(e.error_kind) = '')::numeric, 0::numeric),
      (7, 'run success con failure_stage valorizzato (atteso NULL, ADR-0034)',
          (SELECT count(*) FROM r WHERE status = 'success'
             AND failure_stage IS NOT NULL)::numeric, 0::numeric),
      (8, 'run in altri stati non-success (partial/running/skipped/missed) — da guardare',
          (SELECT count(*) FROM r
            WHERE status NOT IN ('success','failed','timeout'))::numeric, NULL::numeric),
      (9, 'righe errors non abbinabili (run_id NULL, fallimento pre-create_run)',
          (SELECT count(*) FROM err WHERE run_id IS NULL)::numeric, NULL::numeric),
     (10, 'righe errors MissedTick (contesto assente: nessuna riga runs creata)',
          (SELECT count(*) FROM errors
            WHERE experiment_id = :'experiment_id'::uuid
              AND error_kind = 'MissedTick')::numeric, NULL::numeric)
)
SELECT criterio, controllo, valore, atteso, esito FROM (
    SELECT ord, 'C7' AS criterio, controllo, valore, atteso,
           CASE WHEN atteso IS NULL THEN 'info'
                WHEN valore = atteso THEN 'PASS'
                ELSE '*** FAIL ***' END AS esito
    FROM chk
    UNION ALL
    SELECT 99, 'C7', 'ESITO COMPLESSIVO', NULL, NULL,
           CASE WHEN EXISTS (SELECT 1 FROM chk WHERE atteso IS NOT NULL AND valore <> atteso)
                THEN '*** FAIL ***' ELSE 'PASS' END
) x ORDER BY x.ord;

-- C7-det — i fallimenti silenziosi, uno per riga (la lista colpevoli del criterio).
\echo '--- C7-det — run failed/timeout senza riga errors (max 50) -------------------'
SELECT
    'C7-det'                              AS criterio,
    r.model_id                            AS modello,
    r.id                                  AS run_id,
    r.tick_id,
    r.scheduled_for,
    r.status                              AS stato_run,
    coalesce(r.failure_stage, '(nullo)')  AS failure_stage
FROM runs r
WHERE r.experiment_id = :'experiment_id'::uuid
  AND r.scheduled_for >= :'window_start'::timestamptz
  AND r.scheduled_for <  :'window_end'::timestamptz
  AND r.status IN ('failed', 'timeout')
  AND NOT EXISTS (
      SELECT 1 FROM errors e
       WHERE e.run_id = r.id
         AND e.error_kind NOT IN ('ChainDivergence', 'MissedTick'))
ORDER BY r.scheduled_for
LIMIT 50;

-- C7-map — mappa failure_stage × error_kind: rende leggibile la classificazione dei fallimenti
-- (per r2: LLMError/LLMRateLimitError dal 07/08, nota r2 §3).
\echo '--- C7-map — failure_stage × error_kind per modello (max 50) ----------------'
SELECT
    'C7-map'                                AS criterio,
    r.model_id                              AS modello,
    r.status                                AS stato_run,
    coalesce(r.failure_stage, '(nullo)')    AS failure_stage,
    coalesce(e.error_kind, '(nessuna riga)') AS error_kind,
    count(*)                                AS n_run
FROM runs r
LEFT JOIN errors e
       ON e.run_id = r.id
      AND e.error_kind NOT IN ('ChainDivergence', 'MissedTick')
WHERE r.experiment_id = :'experiment_id'::uuid
  AND r.scheduled_for >= :'window_start'::timestamptz
  AND r.scheduled_for <  :'window_end'::timestamptz
  AND r.status IN ('failed', 'timeout')
GROUP BY r.model_id, r.status, r.failure_stage, e.error_kind
ORDER BY r.model_id, n_run DESC
LIMIT 50;


-- =============================================================================================
-- C8 — DASHBOARD SENZA BUCHI
--   Testo (§3): «Equity curve continue per i 4 modelli + 3 baseline; pagina Decisions con Result
--   etichettato (PnL / no position / failed / pending — no "–" indistinti); Open Exposure e PnL
--   Realized/Unrealized coerenti col DB.»
--
--   *** NON VERIFICABILE IN SQL. ***  C8 è un criterio sull'INTERFACCIA. La dashboard è un
--   artefatto di deploy che vive FUORI da questo monorepo (censimento in ADR-0039: «dashboard —
--   fuori repo (artefatto di deploy separato)»), quindi nessuna query può dire se una curva è
--   renderizzata continua, se la colonna Result mostra un'etichetta o un «–», o se il pannello
--   Open Exposure concorda con ciò che il DB contiene. L'esito di C8 resta **N/A** qui.
--
--   COME SI VERIFICA DAVVERO: ispezione visiva della dashboard su Railway
--   (progetto aiat-m6 / environment production / servizio dashboard), con l'esperimento
--   selezionato e la finestra impostata sulle 48h; si guardano (i) le 7 curve equity — 4 modelli
--   + 3 baseline — cercando interruzioni; (ii) la pagina Decisions, verificando che ogni riga
--   abbia un Result etichettato e non un «–»; (iii) Open Exposure e PnL Realized/Unrealized,
--   confrontandoli con i valori del DB. L'evidenza è uno screenshot, non un output di psql.
--
--   La query qui sotto NON verifica C8: verifica solo che i DATI SOTTO la dashboard esistano e
--   siano continui — condizione necessaria, non sufficiente. Se questi controlli falliscono, la
--   dashboard avrà certamente buchi; se passano, la dashboard può comunque averne per ragioni
--   di rendering. Tutte le righe hanno esito «info» per questo motivo.
-- =============================================================================================
\echo ''
\echo '--- C8 — dashboard: NON verificabile in SQL (esito N/A) ----------------------'
\echo '    Verifica per ispezione visiva; sotto solo i DATI sottostanti (condizione necessaria).'
WITH tick AS (
    SELECT cs.tick_id, cs.tick_at
    FROM context_snapshots cs
    WHERE cs.experiment_id = :'experiment_id'::uuid
      AND cs.tick_at >= :'window_start'::timestamptz
      AND cs.tick_at <  :'window_end'::timestamptz
), modelli AS (
    SELECT DISTINCT model_id FROM runs WHERE experiment_id = :'experiment_id'::uuid
), chk(ord, controllo, valore, atteso) AS (
    VALUES
      (1, 'tick (context_snapshots) in finestra',
          (SELECT count(*) FROM tick)::numeric, NULL::numeric),
      (2, 'modelli con run nell''esperimento (attesi 4)',
          (SELECT count(*) FROM modelli)::numeric, NULL::numeric),
      (3, 'baseline_configs dell''esperimento (attesi 3)',
          (SELECT count(*) FROM baseline_configs
            WHERE experiment_id = :'experiment_id'::uuid)::numeric, NULL::numeric),
      (4, 'run success/partial SENZA account_snapshot (sorgente equity per-modello)',
          (SELECT count(*) FROM runs r
            WHERE r.experiment_id = :'experiment_id'::uuid
              AND r.scheduled_for >= :'window_start'::timestamptz
              AND r.scheduled_for <  :'window_end'::timestamptz
              AND r.status IN ('success','partial')
              AND NOT EXISTS (SELECT 1 FROM account_snapshots a
                               WHERE a.run_id = r.id))::numeric, NULL::numeric),
      (5, 'coppie (tick × baseline) attese',
          ((SELECT count(*) FROM tick)
           * (SELECT count(*) FROM baseline_configs
               WHERE experiment_id = :'experiment_id'::uuid))::numeric, NULL::numeric),
      (6, 'coppie (tick × baseline) SENZA snapshot equity  [buchi nelle 3 curve baseline]',
          ((SELECT count(*) FROM tick)
             * (SELECT count(*) FROM baseline_configs
                 WHERE experiment_id = :'experiment_id'::uuid)
           - (SELECT count(*) FROM baseline_equity_snapshots b
               JOIN tick t ON t.tick_id = b.tick_id
              WHERE b.experiment_id = :'experiment_id'::uuid))::numeric, NULL::numeric),
      (7, 'decision_actions con execution_status ''pending'' residuo',
          (SELECT count(*) FROM decision_actions
            WHERE experiment_id = :'experiment_id'::uuid
              AND execution_status = 'pending')::numeric, NULL::numeric)
)
SELECT criterio, controllo, valore, atteso, esito FROM (
    SELECT ord, 'C8' AS criterio, controllo, valore, atteso, 'info' AS esito FROM chk
    UNION ALL
    SELECT 99, 'C8', 'ESITO COMPLESSIVO', NULL, NULL,
           'N/A — non verificabile in SQL (ispezione visiva sulla dashboard)'
) x ORDER BY x.ord;

-- C8-sup — i tick in cui manca almeno uno dei 3 snapshot baseline: sono i buchi che la
-- dashboard renderizzerebbe come interruzioni delle curve non-LLM.
\echo '--- C8-sup — tick con snapshot baseline mancanti (max 50) -------------------'
SELECT
    'C8-sup'                                                    AS criterio,
    cs.tick_id,
    cs.tick_at,
    count(b.id)                                                 AS n_baseline_presenti,
    (SELECT count(*) FROM baseline_configs
      WHERE experiment_id = :'experiment_id'::uuid)             AS n_baseline_attesi,
    'info'                                                      AS esito
FROM context_snapshots cs
LEFT JOIN baseline_equity_snapshots b
       ON b.experiment_id = cs.experiment_id
      AND b.tick_id = cs.tick_id
WHERE cs.experiment_id = :'experiment_id'::uuid
  AND cs.tick_at >= :'window_start'::timestamptz
  AND cs.tick_at <  :'window_end'::timestamptz
GROUP BY cs.tick_id, cs.tick_at
HAVING count(b.id) < (SELECT count(*) FROM baseline_configs
                       WHERE experiment_id = :'experiment_id'::uuid)
ORDER BY cs.tick_at
LIMIT 50;


-- =============================================================================================
-- C9 — COSTI LLM TRACCIATI
--   Testo (§3): «`cost_events` popolati per ogni run success; se l'indagine `reasoning_tokens=0`
--   (Opus) conferma la sottostima, il fix è pre-M7 perché i costi sono un dato della tesi.»
--
--   COME SI LEGGE: PASS quando `run success SENZA cost_event` = 0 (la prima metà del criterio,
--   l'unica che sia una condizione sul DB). Finestra su `runs.scheduled_for`.
--   SECONDA METÀ NON VERIFICABILE IN SQL: se `reasoning_tokens = 0` sia una SOTTOSTIMA o il
--   valore corretto non si decide leggendo la tabella — `cost_events.reasoning_tokens` registra
--   quello che il provider ha riportato nella response. Stabilire se il provider ha riportato
--   male (o se il client non legge il campo giusto) richiede di confrontare una risposta grezza
--   / la console di fatturazione del provider con la riga persistita. Lo spot-check è ancora da
--   fare: la nota r2 §8 punto 6 lo registra come «non risulta registrato per nessuna delle due
--   esecuzioni». Qui sotto c'è solo il CONTEGGIO per modello, che dice dove guardare.
--   Nota: i run `partial` invocano l'LLM esattamente come i `success` (il downgrade avviene allo
--   step [10], dopo il persist atomico del passo [7]) e quindi hanno anch'essi un `cost_event`:
--   sono contati a parte perché il criterio nomina `success`.
-- =============================================================================================
\echo ''
\echo '--- C9 — costi LLM tracciati ------------------------------------------------'
WITH r AS (
    SELECT * FROM runs
    WHERE experiment_id = :'experiment_id'::uuid
      AND scheduled_for >= :'window_start'::timestamptz
      AND scheduled_for <  :'window_end'::timestamptz
), ce AS (
    SELECT run_id, count(*) AS n, sum(cost_usd) AS costo
    FROM cost_events
    WHERE experiment_id = :'experiment_id'::uuid
    GROUP BY run_id
), chk(ord, controllo, valore, atteso) AS (
    VALUES
      (1, 'run success in finestra',
          (SELECT count(*) FROM r WHERE status = 'success')::numeric, NULL::numeric),
      (2, 'run success SENZA riga cost_events',
          (SELECT count(*) FROM r LEFT JOIN ce ON ce.run_id = r.id
            WHERE r.status = 'success' AND coalesce(ce.n, 0) = 0)::numeric, 0::numeric),
      (3, 'run success con PIÙ di una riga cost_events',
          (SELECT count(*) FROM r JOIN ce ON ce.run_id = r.id
            WHERE r.status = 'success' AND ce.n > 1)::numeric, 0::numeric),
      (4, 'run success SENZA riga decisions',
          (SELECT count(*) FROM r
            WHERE r.status = 'success'
              AND NOT EXISTS (SELECT 1 FROM decisions d WHERE d.run_id = r.id))::numeric,
          0::numeric),
      (5, 'run success con cost_usd = 0',
          (SELECT count(*) FROM r JOIN ce ON ce.run_id = r.id
            WHERE r.status = 'success' AND ce.costo = 0)::numeric, NULL::numeric),
      (6, 'run partial SENZA riga cost_events (informativa: anche partial invoca l''LLM)',
          (SELECT count(*) FROM r LEFT JOIN ce ON ce.run_id = r.id
            WHERE r.status = 'partial' AND coalesce(ce.n, 0) = 0)::numeric, NULL::numeric),
      (7, 'costo LLM totale in finestra (USD)',
          (SELECT coalesce(sum(ce.costo), 0) FROM r JOIN ce ON ce.run_id = r.id)::numeric,
          NULL::numeric),
      (8, 'cost_events orfani (run di altro esperimento)',
          (SELECT count(*) FROM cost_events c
             LEFT JOIN runs rr ON rr.id = c.run_id
                              AND rr.experiment_id = :'experiment_id'::uuid
            WHERE c.experiment_id = :'experiment_id'::uuid
              AND rr.id IS NULL)::numeric, 0::numeric)
)
SELECT criterio, controllo, valore, atteso, esito FROM (
    SELECT ord, 'C9' AS criterio, controllo, valore, atteso,
           CASE WHEN atteso IS NULL THEN 'info'
                WHEN valore = atteso THEN 'PASS'
                ELSE '*** FAIL ***' END AS esito
    FROM chk
    UNION ALL
    SELECT 99, 'C9', 'ESITO COMPLESSIVO (solo prima metà: cost_events per ogni run success)',
           NULL, NULL,
           CASE WHEN EXISTS (SELECT 1 FROM chk WHERE atteso IS NOT NULL AND valore <> atteso)
                THEN '*** FAIL ***' ELSE 'PASS' END
) x ORDER BY x.ord;

-- C9-det — i run success senza cost_event, uno per riga.
\echo '--- C9-det — run success senza cost_events (max 50) -------------------------'
SELECT
    'C9-det'          AS criterio,
    r.model_id        AS modello,
    r.id              AS run_id,
    r.tick_id,
    r.scheduled_for,
    EXISTS (SELECT 1 FROM decisions d WHERE d.run_id = r.id) AS ha_decision
FROM runs r
WHERE r.experiment_id = :'experiment_id'::uuid
  AND r.scheduled_for >= :'window_start'::timestamptz
  AND r.scheduled_for <  :'window_end'::timestamptz
  AND r.status = 'success'
  AND NOT EXISTS (SELECT 1 FROM cost_events c WHERE c.run_id = r.id)
ORDER BY r.scheduled_for
LIMIT 50;

-- C9-tok — token e costi per modello. `run_con_reasoning_zero` è il PUNTO DI PARTENZA
-- dell'indagine `reasoning_tokens=0` che il criterio nomina: NON è un verdetto — un modello
-- non-thinking ha legittimamente 0. Il confronto va fatto con la console di fatturazione del
-- provider (`models.pricing_reasoning_usd_per_1m` dice quali modelli sono tariffati sul
-- reasoning e quindi per quali uno 0 sarebbe sospetto).
\echo '--- C9-tok — token/costi per modello (base dell''indagine reasoning_tokens) --'
SELECT
    'C9-tok'                                                  AS criterio,
    c.model_id                                                AS modello,
    m.model_name_api                                          AS modello_api,
    m.pricing_reasoning_usd_per_1m                            AS prezzo_reasoning_1m,
    count(*)                                                  AS n_cost_events,
    sum(c.input_tokens)                                       AS input_tokens,
    sum(c.output_tokens)                                      AS output_tokens,
    sum(c.reasoning_tokens)                                   AS reasoning_tokens,
    count(*) FILTER (WHERE c.reasoning_tokens = 0)            AS run_con_reasoning_zero,
    sum(c.cost_usd)                                           AS costo_usd,
    'info'                                                    AS esito
FROM cost_events c
JOIN runs r  ON r.id = c.run_id
JOIN models m ON m.id = c.model_id
WHERE c.experiment_id = :'experiment_id'::uuid
  AND r.scheduled_for >= :'window_start'::timestamptz
  AND r.scheduled_for <  :'window_end'::timestamptz
GROUP BY c.model_id, m.model_name_api, m.pricing_reasoning_usd_per_1m
ORDER BY c.model_id;


\echo ''
\echo '#############################################################################'
\echo '# FINE BATTERIA C1-C9.'
\echo '# Il gate è VERDE solo se C1-C7 e C9 sono tutti PASS (M6.2-PLAN.md §3).'
\echo '# C8 resta N/A: va verificato per ispezione visiva sulla dashboard.'
\echo '# Di C3 questa batteria copre la sola presenza delle righe: il segno va confrontato'
\echo '# a campione con l''export HL (CSV in CEST, DB in UTC) usando il blocco C3-hl.'
\echo '# Di C9 copre la sola prima metà: l''indagine reasoning_tokens=0 è extra-SQL (C9-tok).'
\echo '# Incollare questo output in docs/M6.2-PLAN.md §7 come evidenza (§4 punto 2).'
\echo '#############################################################################'
