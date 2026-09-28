# STATISTICS — agreed specification, 2026-09-28

Status: planned, not implemented by the reconnect/authentication release.
Execution and deployment take place on Hostinger; local Docker is not used.

## Signal outcomes

Independent analytics, without changes to Pine Research Mode or source formulas.
WE and PINE READY use distinct detector events (not panel state or Telegram deliveries).
Freeze event ID, generation, family, symbol, TF, direction, entry, initial SL/T1,
ATR, quality metrics, data quality, engine/parameter versions and timestamps.
An evaluation is unique by detector event ID and evaluation-policy version.

WIN means a favorable 2% price move before initial SL; LONG target entry*1.02,
SHORT target entry*0.98. LOSS means initial SL first. At 2%, signal success is
permanent and the separate virtual management model moves SL to entry.
Returning to entry is WIN signal / BE exit, not a realized +2% profit.
Fees/slippage and actual executions must not be inferred from this label.
T1-before-SL is an additional metric, not a substitute for the 2% outcome.

Other outcomes: OPEN, EXPIRED, AMBIGUOUS, DATA_GAP and INVALID.
Default observation horizon proposed: 24 real hours; optional 4/12/48 hours.
Only post-event prices count. If SL and target occur in the same candle, resolve
ordering using lower-TF/trade evidence; otherwise mark AMBIGUOUS.
Incomplete observation is not EXPIRED. Never infer an optimistic intrabar path.

## Aggregation and interface

Cohort uses signal creation time in the configured display timezone (UTC storage).
The cohort end date does not truncate subsequent outcome observation.
Resolved winrate = WIN/(WIN+LOSS); mature horizon success =
WIN/(WIN+LOSS+EXPIRED), using complete observations only. Always display sample
size, unresolved cases and coverage; empty denominators produce no-data, not 0%.
Separate live/replay/reference data and parameter/evaluation versions.

Filters: date presets/custom range, WE/PINE READY, direction, symbol, TF, path,
parameter version and data quality. Show comparison cards, breakdowns, time to
2%/SL, MFE/MAE, paginated event details, chart links and CSV export.
Aggregate the entire SQL cohort independently of pagination or API item limits.
WE and READY for one generation remain separate evaluations, not two independent
portfolio trades. Track open evaluations even after liquidity-universe exclusion.

## Implementation sequence

1. Restore timely market coverage and keep public administration authenticated.
2. Add immutable event capture and independent durable observation/outcome tables.
3. Add replay-safe/idempotent tracker, first-hit ordering, gap handling and restart tests.
4. Add server-side aggregation, pagination, date boundaries and policy versioning.
5. Add STATISTICS UI and exports; backfill only evidence-complete historical cases.
6. Validate LONG/SHORT boundaries, same-candle ambiguity, duplicate events, pool
   changes, restart and parameter changes. Never alter baseline Pine checkpoints.
