# R3_COMPLETENESS_EXCEPTION_ADJUDICATION_V01

Bounded offline adjudication of the two canonical-on-NOT_EXPECTED keys and the frozen 39 UNKNOWN session keys.

## Decision

- `CANONICAL_VALID_N=1`, `CANONICAL_INVALID_N=0`, `CANONICAL_UNRESOLVED_N=1`.
- `UNKNOWN_INPUT_N=39`, resolved expected=`0`, resolved not-expected=`0`, remaining=`39`.
- `R3_REFREEZE_RECOMMENDATION=BLOCKED`; no repair or deletion was executed.

## Frozen authority and exact reconciliation

- `BASE_HEAD=0e7c290e0a5e80eb44e90192405a716fe2f6dce8`; report does not claim a future commit SHA (`REPORT_COMMIT_NOT_CLAIMED=true`).
- input manifest: `2580` files / `ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731`.
- session authority: `10897229` keys / `0dfe773329893c261240f919d08a7b0fc2346523ff05a45e9b20201b106f0a47`.
- expected / not-expected / unknown: `10709989` / `187201` / `39`.
- canonical rows / unique / duplicate: `10709991` / `10709991` / `0`.
- missing expected=`0`, on not-expected=`2`, on unknown=`0`, outside=`0`.
- additive `UNEXPECTED_CANONICAL_TOTAL_N=2` (`2 + 0 + 0`).

## Recovered R3 row-eligibility contract

R3 has two separate meanings: the frozen structural checker has no zero-value exclusion, while actual-traded completeness coverage requires positive-volume evidence. A zero-volume placeholder is therefore not a traded-session PASS and is not, by itself, a delete instruction.
- `docs/contracts/R3_DAILY_FOUNDATION_CONTRACT.md:15-18,181-182` at `6eb39447`: daily_bars is raw/unadjusted v2 with volume/amount fields; zero-volume placeholders are not coverage evidence.
- `docs/plans/R3_DAILY_FOUNDATION_IMPLEMENTATION_PLAN.md:760-767` at `d13e2ece`: positive-volume coverage is required and suspension cannot be inferred from volume==0.
- `tools/verify_r3_daily_foundation.py:124-169` at `6eb39447`: structural QA rejects negative/null volume and negative amount, but does not reject zero volume or zero amount.
- `reports/implementation/R3_DAILY_COVERAGE_AND_FALLBACK_CONTRACT_V01.md:11-12,55-63,75-78` at `40bd2411`: expected traded keys are independent BaoStock tradestatus==1 keys; UNKNOWN means no repair.

## Two canonical conflicts

### 600651.SH / 2016-08-25

- decision=`CANONICAL_VALID`; authority=`NOT_EXPECTED_BAR` / `PROVIDER_TRADESTATUS_0` / tradestatus=`0`; request=`R3SAC-033283`.
- canonical_source=`tdx_protocol`; adjudication_source=`baostock`; independence=`DIFFERENT_PROVIDER_FAMILIES`.
- reason: TDX canonical row is a structurally accepted zero-volume/zero-amount placeholder; frozen R3 excludes zero-volume placeholders from coverage evidence, while BaoStock tradestatus=0 agrees. Retain the physical row; it is not an EXPECTED_BAR and is not a delete repair target.

Canonical target row:

```json
{
  "amount": 0.0,
  "close": 12.31,
  "data_version": "v2",
  "fetched_at": "2026-08-19 11:46:40.412733+08",
  "high": 12.31,
  "low": 12.31,
  "open": 12.31,
  "partition_file": "curated/daily_bars/trade_date=2016-08-25/part-merged.parquet",
  "preclose": null,
  "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
  "provenance": {
    "data_version": "v2",
    "fetched_at": "2026-08-19 11:46:40.412733+08",
    "source": "tdx_protocol"
  },
  "source": "tdx_protocol",
  "symbol": "600651.SH",
  "trade_date": "2016-08-25",
  "volume": 0
}
```

Provider target facts:

```json
{
  "amount": "0.0000",
  "close": "12.3100",
  "high": "12.3100",
  "low": "12.3100",
  "open": "12.3100",
  "preclose": "12.3100",
  "provider_code": "sh.600651",
  "raw_row": [
    "2016-08-25",
    "sh.600651",
    "12.3100",
    "12.3100",
    "12.3100",
    "12.3100",
    "0",
    "0.0000",
    "12.3100",
    "0"
  ],
  "trade_date": "2016-08-25",
  "tradestatus": 0,
  "volume": "0"
}
```

Adjacent canonical rows:

```json
[
  {
    "amount": 288724992.0,
    "close": 12.89,
    "data_version": "v2",
    "fetched_at": "2026-08-19 11:46:40.412733+08",
    "high": 12.95,
    "low": 12.39,
    "open": 12.45,
    "partition_file": "curated/daily_bars/trade_date=2016-08-18/part-merged.parquet",
    "preclose": null,
    "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
    "provenance": {
      "data_version": "v2",
      "fetched_at": "2026-08-19 11:46:40.412733+08",
      "source": "tdx_protocol"
    },
    "source": "tdx_protocol",
    "symbol": "600651.SH",
    "trade_date": "2016-08-18",
    "volume": 22822400
  },
  {
    "amount": 187338000.0,
    "close": 12.73,
    "data_version": "v2",
    "fetched_at": "2026-08-19 11:46:40.412733+08",
    "high": 12.87,
    "low": 12.51,
    "open": 12.78,
    "partition_file": "curated/daily_bars/trade_date=2016-08-19/part-merged.parquet",
    "preclose": null,
    "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
    "provenance": {
      "data_version": "v2",
      "fetched_at": "2026-08-19 11:46:40.412733+08",
      "source": "tdx_protocol"
    },
    "source": "tdx_protocol",
    "symbol": "600651.SH",
    "trade_date": "2016-08-19",
    "volume": 14742600
  },
  {
    "amount": 134427008.0,
    "close": 12.38,
    "data_version": "v2",
    "fetched_at": "2026-08-19 11:46:40.412733+08",
    "high": 12.81,
    "low": 12.3,
    "open": 12.73,
    "partition_file": "curated/daily_bars/trade_date=2016-08-22/part-merged.parquet",
    "preclose": null,
    "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
    "provenance": {
      "data_version": "v2",
      "fetched_at": "2026-08-19 11:46:40.412733+08",
      "source": "tdx_protocol"
    },
    "source": "tdx_protocol",
    "symbol": "600651.SH",
    "trade_date": "2016-08-22",
    "volume": 10708400
  },
  {
    "amount": 94151000.0,
    "close": 12.41,
    "data_version": "v2",
    "fetched_at": "2026-08-19 11:46:40.412733+08",
    "high": 12.47,
    "low": 12.26,
    "open": 12.35,
    "partition_file": "curated/daily_bars/trade_date=2016-08-23/part-merged.parquet",
    "preclose": null,
    "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
    "provenance": {
      "data_version": "v2",
      "fetched_at": "2026-08-19 11:46:40.412733+08",
      "source": "tdx_protocol"
    },
    "source": "tdx_protocol",
    "symbol": "600651.SH",
    "trade_date": "2016-08-23",
    "volume": 7605500
  },
  {
    "amount": 114650000.0,
    "close": 12.31,
    "data_version": "v2",
    "fetched_at": "2026-08-19 11:46:40.412733+08",
    "high": 12.56,
    "low": 12.2,
    "open": 12.41,
    "partition_file": "curated/daily_bars/trade_date=2016-08-24/part-merged.parquet",
    "preclose": null,
    "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
    "provenance": {
      "data_version": "v2",
      "fetched_at": "2026-08-19 11:46:40.412733+08",
      "source": "tdx_protocol"
    },
    "source": "tdx_protocol",
    "symbol": "600651.SH",
    "trade_date": "2016-08-24",
    "volume": 9229200
  }
]
```

### 688065.SH / 2023-06-15

- decision=`UNRESOLVED`; authority=`NOT_EXPECTED_BAR` / `PROVIDER_TRADESTATUS_0` / tradestatus=`0`; request=`R3SAC-045210`.
- canonical_source=`tdx_protocol`; adjudication_source=`baostock`; independence=`DIFFERENT_PROVIDER_FAMILIES_BUT_CONFLICTING_EVIDENCE`.
- reason: BaoStock tradestatus=0 conflicts with a positive-volume TDX canonical row and the provider OHLCV values are not identical. No independent local status authority resolves the conflict; fail closed as UNRESOLVED.

Canonical target row:

```json
{
  "amount": 19589786.0,
  "close": 55.53,
  "data_version": "v2",
  "fetched_at": "2026-08-19 11:56:47.419796+08",
  "high": 55.8,
  "low": 54.76,
  "open": 55.04,
  "partition_file": "curated/daily_bars/trade_date=2023-06-15/part-merged.parquet",
  "preclose": null,
  "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
  "provenance": {
    "data_version": "v2",
    "fetched_at": "2026-08-19 11:56:47.419796+08",
    "source": "tdx_protocol"
  },
  "source": "tdx_protocol",
  "symbol": "688065.SH",
  "trade_date": "2023-06-15",
  "volume": 353400
}
```

Provider target facts:

```json
{
  "amount": "19589785.9900",
  "close": "55.5300",
  "high": "55.8000",
  "low": "54.7600",
  "open": "55.0400",
  "preclose": "55.0200",
  "provider_code": "sh.688065",
  "raw_row": [
    "2023-06-15",
    "sh.688065",
    "55.0400",
    "55.8000",
    "54.7600",
    "55.5300",
    "353426",
    "19589785.9900",
    "55.0200",
    "0"
  ],
  "trade_date": "2023-06-15",
  "tradestatus": 0,
  "volume": "353426"
}
```

Adjacent canonical rows:

```json
[
  {
    "amount": 62967148.0,
    "close": 55.72,
    "data_version": "v2",
    "fetched_at": "2026-08-19 11:56:47.419796+08",
    "high": 56.47,
    "low": 54.99,
    "open": 54.99,
    "partition_file": "curated/daily_bars/trade_date=2023-06-08/part-merged.parquet",
    "preclose": null,
    "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
    "provenance": {
      "data_version": "v2",
      "fetched_at": "2026-08-19 11:56:47.419796+08",
      "source": "tdx_protocol"
    },
    "source": "tdx_protocol",
    "symbol": "688065.SH",
    "trade_date": "2023-06-08",
    "volume": 1126700
  },
  {
    "amount": 72237448.0,
    "close": 55.8,
    "data_version": "v2",
    "fetched_at": "2026-08-19 11:56:47.419796+08",
    "high": 56.66,
    "low": 54.71,
    "open": 55.6,
    "partition_file": "curated/daily_bars/trade_date=2023-06-09/part-merged.parquet",
    "preclose": null,
    "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
    "provenance": {
      "data_version": "v2",
      "fetched_at": "2026-08-19 11:56:47.419796+08",
      "source": "tdx_protocol"
    },
    "source": "tdx_protocol",
    "symbol": "688065.SH",
    "trade_date": "2023-06-09",
    "volume": 1301200
  },
  {
    "amount": 48952280.0,
    "close": 55.31,
    "data_version": "v2",
    "fetched_at": "2026-08-19 11:56:47.419796+08",
    "high": 56.36,
    "low": 54.11,
    "open": 56.36,
    "partition_file": "curated/daily_bars/trade_date=2023-06-12/part-merged.parquet",
    "preclose": null,
    "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
    "provenance": {
      "data_version": "v2",
      "fetched_at": "2026-08-19 11:56:47.419796+08",
      "source": "tdx_protocol"
    },
    "source": "tdx_protocol",
    "symbol": "688065.SH",
    "trade_date": "2023-06-12",
    "volume": 890500
  },
  {
    "amount": 48238432.0,
    "close": 54.47,
    "data_version": "v2",
    "fetched_at": "2026-08-19 11:56:47.419796+08",
    "high": 56.14,
    "low": 54.3,
    "open": 55.7,
    "partition_file": "curated/daily_bars/trade_date=2023-06-13/part-merged.parquet",
    "preclose": null,
    "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
    "provenance": {
      "data_version": "v2",
      "fetched_at": "2026-08-19 11:56:47.419796+08",
      "source": "tdx_protocol"
    },
    "source": "tdx_protocol",
    "symbol": "688065.SH",
    "trade_date": "2023-06-13",
    "volume": 876400
  },
  {
    "amount": 41960420.0,
    "close": 55.02,
    "data_version": "v2",
    "fetched_at": "2026-08-19 11:56:47.419796+08",
    "high": 55.91,
    "low": 54.5,
    "open": 54.85,
    "partition_file": "curated/daily_bars/trade_date=2023-06-14/part-merged.parquet",
    "preclose": null,
    "preclose_status": "NOT_PRESENT_IN_CANONICAL_DAILY_SCHEMA",
    "provenance": {
      "data_version": "v2",
      "fetched_at": "2026-08-19 11:56:47.419796+08",
      "source": "tdx_protocol"
    },
    "source": "tdx_protocol",
    "symbol": "688065.SH",
    "trade_date": "2023-06-14",
    "volume": 758400
  }
]
```

## 39 UNKNOWN keys

Each key was checked against its frozen receipt, lifecycle row, and canonical absence; no provider/network request was made.

| symbol | trade_date | request_id | decision | evidence level |
|---|---|---|---|---|
| 000004.SZ | 2026-07-14 | R3SAC-000033 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 000584.SZ | 2025-07-11 | R3SAC-001778 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 000622.SZ | 2025-07-16 | R3SAC-002097 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 000627.SZ | 2025-09-30 | R3SAC-002140 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 000638.SZ | 2026-06-03 | R3SAC-002250 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 000851.SZ | 2025-11-11 | R3SAC-003763 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 002231.SZ | 2026-03-27 | R3SAC-007946 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 002336.SZ | 2025-07-04 | R3SAC-009077 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 002750.SZ | 2025-06-27 | R3SAC-013475 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 002808.SZ | 2026-07-14 | R3SAC-014045 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 002898.SZ | 2026-07-17 | R3SAC-014911 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 300029.SZ | 2026-07-10 | R3SAC-016275 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 300108.SZ | 2025-05-29 | R3SAC-017109 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 300208.SZ | 2025-07-21 | R3SAC-018179 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 300280.SZ | 2025-10-14 | R3SAC-018959 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 300344.SZ | 2026-04-22 | R3SAC-019641 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 300379.SZ | 2026-01-22 | R3SAC-019994 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 300391.SZ | 2026-04-13 | R3SAC-020126 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 300630.SZ | 2025-05-22 | R3SAC-022642 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600190.SH | 2025-07-25 | R3SAC-029227 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600193.SH | 2026-07-06 | R3SAC-029260 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600200.SH | 2025-12-31 | R3SAC-029325 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600355.SH | 2026-04-27 | R3SAC-030717 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600387.SH | 2025-07-11 | R3SAC-031020 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600421.SH | 2026-06-26 | R3SAC-031285 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600462.SH | 2025-07-21 | R3SAC-031540 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600599.SH | 2026-06-26 | R3SAC-032799 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600608.SH | 2026-07-03 | R3SAC-032887 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600636.SH | 2026-06-29 | R3SAC-033141 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600696.SH | 2026-06-29 | R3SAC-033709 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600705.SH | 2025-05-27 | R3SAC-033791 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 600804.SH | 2025-07-03 | R3SAC-034782 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 601028.SH | 2025-05-27 | R3SAC-036512 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 601989.SH | 2025-09-05 | R3SAC-038458 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 603003.SH | 2025-07-03 | R3SAC-038583 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 603056.SH | 2026-03-31 | R3SAC-039038 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 603388.SH | 2025-12-05 | R3SAC-041145 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 605081.SH | 2026-07-03 | R3SAC-044242 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |
| 688287.SH | 2026-06-10 | R3SAC-046414 | UNKNOWN | LOCAL_FROZEN_RECEIPT_ONLY |

## Safety and verification

- `FULL_EXTRACTION_EXECUTED=false`
- `NETWORK_PROVIDER_DATA_FETCH=NO`
- `BAOSTOCK_EXECUTED=false`
- `TDX_EXECUTED=false`
- `CANONICAL_WRITE_EXECUTED=false`
- `CANONICAL_BYTES_MUTATED=false`
- `R4A9_CHECKPOINT_MUTATED=false`
- `R4A9_RESUME_AUTHORIZED=false`
- `PRECLOSE_COMPLETE=false`
- `PRODUCTION=false`
- `FORWARD=false`
- `TRADEPLAN=false`

- `NETWORK_PROVIDER_REQUEST_N=0`.
- `TEST_RESULT=32 passed in 0.02s`; `PY_COMPILE=PASS`; `GIT_DIFF_CHECK=PASS`.
