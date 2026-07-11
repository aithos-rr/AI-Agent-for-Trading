"""STUB — backfill missing fee_events for pre-fix positions (finding A).

NOT IMPLEMENTED ON PURPOSE. This is a documented recipe, not a runnable backfill: it
refuses to touch the DB. Backfilling live trading bookkeeping is a manual,
review-gated operation and MUST NOT be a side effect of importing/running a script.

Context
-------
Before the finding-A fix, ``RealHyperliquidClient`` hard-coded ``OrderResult.fee_usd=None``
for entry and model-close orders, so ``PositionsRepository`` wrote no ``fee_events`` and the
189 M6 outcomes all carry ``sum_fees_usd = 0`` (net PnL systematically overstated). The fix
reconciles fees going FORWARD; it does not rewrite history. This stub records how to repair
the historical rows if the thesis needs fee-accurate PnL for the M6 window.

Procedure (for whoever runs this, after review)
------------------------------------------------
1. Pull the full fill history from Hyperliquid testnet for each model wallet:
   ``info.user_fills_by_time(address, start_ms, end_ms)`` (paginated; ``user_fills`` alone
   returns only the most recent ~2000). Each fill has ``oid`` and ``fee`` (key ``"fee"``).
2. Build ``oid -> sum(fee)`` across all fills (an order can fill in several partials).
3. For each ``orders`` row in the M6 window with ``hl_order_id`` in that map and NO existing
   ``fee_events`` row, insert a ``FeeEvent`` (``fee_type`` via the same ENTRY→taker_open /
   else→taker_close rule as ``PositionsRepository._fee_type``; ``occurred_at`` = the order's
   ``filled_at``; link ``order_id``/``position_id``/``run_id``/``experiment_id``/``model_id``).
   Autonomous SL/TP-trigger closures have no close order row (ADR-0025/0030) — their fee
   backfill is part of that separate deferred trigger-reconciliation, not this pass.
4. Recompute each affected ``outcomes`` row:
   ``sum_fees_usd = sum(fee_events.fee_usd WHERE position_id = ...)``,
   ``pnl_net_fee_usd = realized_pnl_gross_usd - sum_fees_usd``,
   ``pnl_net_fee_funding_usd = pnl_net_fee_usd - sum_funding_usd``,
   ``was_profitable_net = pnl_net_fee_funding_usd > 0``.
5. Do it inside ONE transaction per model, dry-run first (print the deltas), and keep a
   before/after snapshot for the thesis appendix. NO schema change — every table exists.

Invariants to honor: #12 (Decimal only — read ``fee`` via ``Decimal(str(...))``), #1
(filter every query by ``model_id``), #9 (testnet only).
"""

from __future__ import annotations

import sys


def main() -> None:
    sys.stderr.write(
        "backfill_fees.py is an intentional stub — see the module docstring for the "
        "review-gated procedure. It does not modify the database. Exiting.\n"
    )
    raise SystemExit(2)


if __name__ == "__main__":
    main()
