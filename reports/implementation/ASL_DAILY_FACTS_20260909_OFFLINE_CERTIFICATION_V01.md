# ASL Daily Facts 2026-09-09 offline certification V01

## Result

`BLOCKED_CERTIFICATION`. No Daily Facts publication was attempted and the R3
daily authority was not changed.

## Frozen input

| Item | Value |
|---|---:|
| Scope | `FULL_ELIGIBLE_ONE_DAY`, 2026-09-09 |
| Eligible keys | 5,208 |
| Terminal RAW records | 5,208 |
| Ledger terminal state | `QUALITY_PASS=5208` after offline postprocess |
| Ledger SHA-256 | `743bc9fbf9c9fc63287971165236492ebe5e3c4ce66933632d0ec66bc1835a0d` |
| RAW file count | 5,208 |
| RAW manifest SHA-256 | `ff08b9e246943628c4c04326b2e4bef6f6d6803b8218b1d40b95c3c33a73678d` |
| Provider network calls during certification | 0 |
| R3 manifest binding | `91848857b69115679bcabf730265a1de1b2f1d8f466a8bd80b5da7dd01e3188e` |

The RAW contract is the pinned BaoStock Phase 1 provider schema and records
the provider version, field contract, raw values, request range, and fetch
timestamp for every persisted symbol.

## Exact certification counts

| Check | Count |
|---|---:|
| ELIGIBLE_N | 5,208 |
| RAW_TERMINAL_N | 5,208 |
| NORMALIZED_ROW_N | 5,208 |
| DUPLICATE_PK_N | 0 |
| MISSING_PK_N | 0 |
| EXTRA_PK_N | 0 |
| SOURCE_ERROR_N | 0 |
| UNKNOWN_N | 0 |
| IDENTITY_MISMATCH_N | 0 |
| DATE_MISMATCH_N | 0 |
| PRECLOSE_UNRESOLVED_N | **16** |
| PCT_CHG_UNRESOLVED_N | 0 |
| TRADE_STATUS_CONFLICT_N | 0 |
| IS_ST_UNKNOWN_N | 0 |
| INVALID_NUMERIC_N | 0 |
| INVALID_DOMAIN_N | 0 |
| PROVENANCE_FAILURE_N | 0 |
| NULL_NUMERIC_N | 10 |

`NULL_NUMERIC_N` is reported separately: these are BaoStock nullable
`pct_chg`/`turnover_rate` values for records labeled `SUSPENDED`. They were not
coerced to zero. The same ten keys have zero-volume/zero-amount R3
carry-forward bars with all OHLC equal to the BaoStock preclose, so they are
valid suspended sessions rather than traded-bar conflicts.

## Preclose unresolved keys

All sixteen rows are `TRADING`, have provider/R3 pct-change reconciliation
`MATCH`, and have a preclose mismatch versus the previous published R3 close.
No exact-date corporate-action/reference-price evidence was supplied, so none
may be reclassified as resolved.

| Symbol | BaoStock preclose | Prior R3 close | Difference |
|---|---:|---:|---:|
| 001400.SZ | 79.67 | 80.17 | -0.50 |
| 002073.SZ | 5.86 | 5.88 | -0.02 |
| 002315.SZ | 25.48 | 25.98 | -0.50 |
| 002322.SZ | 12.38 | 12.71 | -0.33 |
| 002441.SZ | 8.18 | 8.38 | -0.20 |
| 002833.SZ | 18.61 | 18.91 | -0.30 |
| 002841.SZ | 46.90 | 47.40 | -0.50 |
| 300196.SZ | 16.70 | 16.90 | -0.20 |
| 300622.SZ | 15.46 | 15.62 | -0.16 |
| 301151.SZ | 22.38 | 22.58 | -0.20 |
| 600114.SH | 28.73 | 28.83 | -0.10 |
| 603992.SH | 20.02 | 20.30 | -0.28 |
| 603993.SH | 18.85 | 18.94 | -0.09 |
| 605377.SH | 8.02 | 8.22 | -0.20 |
| 688128.SH | 21.89 | 22.14 | -0.25 |
| 688271.SH | 106.87 | 107.00 | -0.13 |

## Trade-status diagnosis

BaoStock returned `SUSPENDED` for the following keys, each with a 2026-09-09
R3 zero-volume/zero-amount carry-forward bar: `000016.SZ`, `002731.SZ`,
`002870.SZ`, `002998.SZ`, `301139.SZ`, `600825.SH`, `600929.SH`,
`605577.SH`, `688291.SH`, `688432.SH`. The certification contract now
distinguishes these from positive-activity R3 bars, which remain fail-closed.

## Authority decision

- Facts manifest: not created.
- Facts promotion receipt: not created.
- Facts authority pointer: unchanged (existing vertical slice only).
- `FACTS_READY`: false.
- `PRECLOSE_COMPLETE`: false.
- R3 daily authority: unchanged.

The smallest next step is a bounded, evidence-first diagnosis of the sixteen
preclose keys and ten trade-status conflicts. It must not refetch the frozen
RAW or publish partial facts.
