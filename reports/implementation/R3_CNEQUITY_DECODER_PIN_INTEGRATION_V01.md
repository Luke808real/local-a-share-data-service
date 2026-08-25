# R3 CNEQUITY DECODER PIN INTEGRATION - V01 (author report)

DATE: 2026-08-25
BASE_HEAD: 29d90638ee35fb5d6fea1a06169852c14306f3ee

## Dependency pin migration

OLD_CNEQUITY_PIN = rootSunc/CNEquity@a18ee0484dfb0801650175471724def3228b8a17
NEW_CNEQUITY_PIN = Luke808real/CNEquity@ecf57023d57dcf925e9da3aa0e023492abb1221d

pyproject.toml updated to exact SHA; uv.lock regenerated (uv lock) with no
rootSunc/a18ee residual. No floating refs.

## LOCKFILE + RUNTIME AUTHORITY

uv.lock source: https://github.com/Luke808real/CNEquity.git?rev=ecf57023...
Runtime: site-packages/cnequity-0.7.2.dist-info/direct_url.json records
url=Luke808real/CNEquity.git commit_id=ecf57023d57dcf925e9da3aa0e023492abb1221d.
CNEQUITY_RUNTIME_COMMIT=ecf57023...

## Decoder contract proof (installed runtime)

category==9 float32: 0x41800000->16.0, 0x3f800000->1.0, 0x42300000->44.0,
0x4949d320->826674.0
non-daily categories [0,1,2,3,8]: 0x41800000->2056.0 (BASE packed get_volume)

## Targeted tests

service targeted (preclose adapter + orchestrator + audit tools): 118 passed
r3_daily_foundation: 3 failures are RUNTIME_CONTRACT_DRIFT because
r3_daily.PINNED_CNEQUITY_SHA remains hardcoded a18ee048 (pin contract test).
Not patched (task: NO production source changes); requires Sol-authorized
PINNED_CNEQUITY_SHA update in a later task. project_docs_contract 1 failure
is pre-existing AS_OF mismatch.

## 300546 bounded live reproduction (local service runtime path)

2016-09-28 volume=3700 (expected 3700) OK
2016-09-29 volume=100  (expected 100)  OK
2016-09-30 volume=1600 (expected 1600) OK
2016-10-10 volume=100  (expected 100)  OK
2016-10-11 volume=4400 (expected 4400) OK

All five rows: VWAP=amount/volume within OHLC; HARD anomaly cleared.
2016-09-29/2016-10-10 rows returned by fixed provider path; NOT inserted
(separate authority decision).

## Non-daily regression

NON_DAILY_SECURITY_BAR_CATEGORY_PARITY=true (category 8 etc. keep BASE
packed 2056.0; upstream minute-bar suite ran 117 passed during V01.x).

## Rebuild feasibility (read-only)

REBUILD_FROM_EXISTING_RAW_POSSIBLE=false   (raw/ is empty)
REBUILD_FROM_EXISTING_STAGING_POSSIBLE=false (staging holds the INCORRECT
  packed-decoded quantities, e.g. 300546 09-30 volume=205600)
NETWORK_REFETCH_REQUIRED=true

Retained layers:
- raw wire quantity: NOT RETAINED
- decoded TDX quantity: staging (INCORRECT, packed-decode x100)
- canonical shares: curated (INCORRECT, same values)

Correcting canonical volume therefore requires a network refetch (or
another provider authority) - no rebuild from retained layers is possible.

## Safety

R3_MARKET_DATA_WRITE=NO
R3_DATA_REBUILD_EXECUTED=false
R4A9_CHECKPOINT_MUTATED=false
R4A9_RESUME_AUTHORIZED=false
PRECLOSE_COMPLETE=false

AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
