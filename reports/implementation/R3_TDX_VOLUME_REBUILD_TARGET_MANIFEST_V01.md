# R3 TDX VOLUME REBUILD TARGET MANIFEST - V01

DATE: 2026-08-25
BASE_HEAD: d716918658b9c3e7fa253fdaf4a01322d772aaa8
AUTH: AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT

Companion JSON: `R3_TDX_VOLUME_REBUILD_TARGET_MANIFEST_V01.json` — every
non-unaffected key, fields:

```text
symbol
trade_date
current_volume
classification            (PROVABLY_AFFECTED | AMBIGUOUS)
corrected_volume_if_provably_affected
source                    (tdx_protocol)
```

Rows sorted by `(symbol, trade_date)`.

## Counts (reproduced, not hardcoded)

- PROVABLY_AFFECTED_N = 32
- AMBIGUOUS_N = 29,267
- REPAIR_SUPERSET_KEY_N = 29,299

## Hashes (SHA-256 over canonical JSON of each subset)

- AFFECTED_KEY_HASH = `131754e24c3fffde5a548acb487746075b381ec012c87cc34d3b9781b7d9abf2`
- AMBIGUOUS_KEY_HASH = `be1911a4c6076348d8af2ed7eded0c728e352913a6be34c76903ec16187d86cd`
- REPAIR_SUPERSET_KEY_HASH = `1d579988015ef61fe06ae0eede91e2c63c2356a966f58363a6ebbd2f126e7e5b`

Canonical serialization: `json.dumps(rows, ensure_ascii=True, sort_keys=True,
separators=(',', ':'))`, rows sorted by `(symbol, trade_date)`.

## Verification

All three hashes recomputed exactly from the persisted manifest rows.

## Exclusion

300546.SZ 2016-09-29 / 2016-10-10 missing-days insertion is a separate
authority decision and is NOT part of this volume manifest.
