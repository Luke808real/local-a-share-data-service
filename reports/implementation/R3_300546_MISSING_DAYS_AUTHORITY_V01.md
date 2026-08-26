# R3 300546 MISSING DAYS AUTHORITY V01

AUTHOR_STATUS: `PASS_PENDING_SOL_AUDIT`

- EXECUTION_BASE_HEAD: `5228dad78e90118eb481a5323f2a4bcce6ac9c28`
- INPUT_FILE_N: 2580
- INPUT_MANIFEST_HASH: `2e0664b4eafb64325691cf2fb8b955a61906222173fc570e755985a604fb3da0`
- SOURCE: `BaoStock`
- SOURCE_ROLE: `BOUNDED_SECONDARY_OHLCV_AUTHORITY_FOR_TDX_HISTORICAL_GAP`
- BAOSTOCK_REQUEST_N: 1
- AUTHORITY_RECEIPT_REUSED: false

## Target facts

- `300546.SZ:2016-09-29` OHLC=(32.36,32.36,32.36,32.36) volume=100 amount=3236.0 preclose=29.42 tradestatus=1 source=baostock data_version=v2
- `300546.SZ:2016-10-10` OHLC=(39.16,39.16,39.16,39.16) volume=100 amount=3916.0 preclose=35.6 tradestatus=1 source=baostock data_version=v2

The BaoStock request was one bounded query for `sz.300546`, 2016-09-29 through 2016-10-10, using the recorded field and adjustment contract. The in-window 2016-09-30 row was used only as a cross-check against the existing canonical row and is not a repair target.

SAFETY: TDX_REFETCH_EXECUTED=false; canonical write=false in this phase; R4A9_RESUME_AUTHORIZED=false; PRECLOSE_COMPLETE=false.
