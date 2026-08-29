# R3_BAOSTOCK_TRADESTATUS0_CONSISTENCY_AUDIT_V01

Offline full internal audit of BaoStock tradestatus=0 rows from the frozen R3 session authority.

## Authority and receipt verification

- `BASE_HEAD=568d0626e501391ed2317425547828f8b6caa56f`; `INPUT_MANIFEST_HASH=ba720d8f75bd3308cab55963df6df64c9ce65c7df86757c8cc58a4e220da5731`.
- `SESSION_AUTHORITY_DATASET_HASH=0dfe773329893c261240f919d08a7b0fc2346523ff05a45e9b20201b106f0a47`; `REQUEST_RECEIPT_INDEX_HASH=0884112d77b75469bc0e4145dfde8ad749b4ae16b7d6974e365029bca799ab8b`.
- receipt pairs verified: `48345`; aggregate hash=`19404ce35db37169c3ec4e2a16dba900979c1b6d74743956c952e440fc234358`.
- status0-relevant receipt pairs: `7053`; aggregate hash=`ce506b42726a70a55bccb4c8bc9426c62f5cec1ebbfe373e112ef1f6127dafdd`.

## Status0 value audit

- `STATUS0_KEY_N=187201`; provider rows present=`187201`.
- zero/positive volume=`172189/8`.
- zero/positive amount=`172191/6`.
- positive volume OR amount=`8`.
- invalid numeric fields=`15004`; valid numeric rows=`172197`.
- contradiction manifest: `8` keys / `6486c6f475c0be95c7882db30a02476cb0974399b0d8d2b6ea43033b3b547e76`.
- semantics status: `REOPENED_CONTRADICTIONS_FOUND`.

`tradestatus=0 -> NOT_EXPECTED_BAR` remains the frozen classification label, but this audit finds whether the same provider rows carry OHLCV values that contradict a zero-trading interpretation. Contradictions are not automatically reclassified.
Zero/positive counts apply only to rows with parseable non-negative numeric volume and amount. Blank provider numeric fields are preserved as invalid evidence and are never coerced to zero.

## Cross-canonical classification

- canonical positive-volume: `1` / `64732868c71a67188c59fd6f9231588dc37147833cac29c78c2f636d95324973`.
- canonical zero-volume: `0` / `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`.
- canonical absent: `7` / `289d927c9871551c509419d49095b6cc8cd3ffd55783d47a670aecf5284c12f0`.

## Anchors

- `688065_CONTRADICTION=true`.
- `600651_ZERO_VOLUME_CONTROL=true`.

```json
{
  "600651_SH_20160825": {
    "canonical_amount": 0.0,
    "canonical_data_version": "v2",
    "canonical_present": true,
    "canonical_source": "tdx_protocol",
    "canonical_volume": 0,
    "provider_amount": "0.0000",
    "provider_tradestatus": 0,
    "provider_volume": 0,
    "symbol": "600651.SH",
    "trade_date": "2016-08-25"
  },
  "688065_SH_20230615": {
    "canonical_amount": 19589786.0,
    "canonical_data_version": "v2",
    "canonical_present": true,
    "canonical_source": "tdx_protocol",
    "canonical_volume": 353400,
    "provider_amount": "19589785.9900",
    "provider_tradestatus": 0,
    "provider_volume": 353426,
    "symbol": "688065.SH",
    "trade_date": "2023-06-15"
  }
}
```

## Current reconciliation impact

- current expected/not-expected/unknown=`10709989/187201/39`.
- potentially misclassified NOT_EXPECTED=`8`.
- `R3_COMPLETENESS_COUNTS_FROZEN=false`; `R3_REFREEZE_RECOMMENDATION=BLOCKED`.

## Safety and verification

- `NETWORK_PROVIDER_DATA_FETCH=NO`
- `BAOSTOCK_EXECUTED=false`
- `TDX_EXECUTED=false`
- `CANONICAL_WRITE_EXECUTED=false`
- `CANONICAL_BYTES_MUTATED=false`
- `R4A9_RESUME_AUTHORIZED=false`
- `PRODUCTION=false`
- `FORWARD=false`
- `TRADEPLAN=false`

- `TEST_RESULT=TARGETED_TESTS_PASS`; `PY_COMPILE=PASS`; `GIT_DIFF_CHECK=PASS`.
- Compact contradiction manifest: `R3_BAOSTOCK_TRADESTATUS0_CONSISTENCY_AUDIT_V01_CONTRADICTIONS.json`.
- Invalid numeric manifest: `R3_BAOSTOCK_TRADESTATUS0_CONSISTENCY_AUDIT_V01_INVALID_NUMERIC.json`.
