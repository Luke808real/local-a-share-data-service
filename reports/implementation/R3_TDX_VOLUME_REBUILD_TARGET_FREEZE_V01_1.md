# R3 TDX VOLUME REBUILD TARGET FREEZE - V01.1

DATE: 2026-08-26
BASE_HEAD: `f99918c786c1f0654ce642a460853141b723f61e`
AUTHOR_STATUS: `PASS_PENDING_SOL_AUDIT`

## Provenance closure

The audited V01.1 classifier was run with the following exact sequence:

```text
PRE = build_input_file_manifest(data_root)
SCAN = scan_canonical(data_root)
POST = build_input_file_manifest(data_root)
```

The freeze is accepted only when `PRE[INPUT_FILE_N]`,
`PRE[INPUT_MANIFEST_HASH]`, and `PRE[FILES]` are each exactly equal to the
corresponding POST value.  The comparison passed:

| field | PRE | POST |
|---|---:|---:|
| INPUT_FILE_N | 2,580 | 2,580 |
| INPUT_MANIFEST_HASH | `f9025a5c...594a6ec` | `f9025a5c...594a6ec` |
| FILES | exact equality | exact equality |

`INPUT_STABLE_DURING_SCAN=true`; `DATA_ROOT_DRIFT=NO`.  The regenerated input
manifest was byte-for-byte identical to the existing V01 manifest, so no
existing input or target manifest was changed.

## Reproduced classifier result

| metric | value |
|---|---:|
| TDX_ROW_N | 10,325,793 |
| PROVABLY_AFFECTED_N | 32 |
| AMBIGUOUS_N | 29,267 |
| PROVABLY_UNAFFECTED_N | 10,296,494 |
| REPAIR_SUPERSET_KEY_N | 29,299 |
| HARD_UNAFFECTED_N | 0 |

The row-count sum equals `TDX_ROW_N`.  The affected, ambiguous, and repair
superset manifest hashes were recomputed from the scan output:

```text
AFFECTED_MANIFEST_HASH=131754e24c3fffde5a548acb487746075b381ec012c87cc34d3b9781b7d9abf2
AMBIGUOUS_MANIFEST_HASH=be1911a4c6076348d8af2ed7eded0c728e352913a6be34c76903ec16187d86cd
REPAIR_SUPERSET_MANIFEST_HASH=1d579988015ef61fe06ae0eede91e2c63c2356a966f58363a6ebbd2f126e7e5b
```

## Provider receipt and cost semantics

`PRIOR_BOUNDED_PROVIDER_RECEIPT_REUSED=true`.  The prior 30-key provider
receipt is carried forward by exact authority:

```text
TARGET_FREEZE_V01_COMMIT=f99918c786c1f0654ce642a460853141b723f61e
SOURCE_REPORT=reports/implementation/R3_TDX_VOLUME_REBUILD_TARGET_FREEZE_V01.json
PROVIDER_VALIDATION_RERUN=false
```

The prior `11,600` value remains only
`TDX_PAGE_REQUEST_ESTIMATE`; it is not an exact hard cap.

## Safety boundary

```text
NETWORK_PROVIDER_DATA_FETCH=NO
R3_MARKET_DATA_WRITE=NO
R3_DATA_REBUILD_EXECUTED=false
R4A9_CHECKPOINT_MUTATED=false
R4A9_RESUME_AUTHORIZED=false
PRECLOSE_COMPLETE=false
```

No repair was performed and no production source was changed.
