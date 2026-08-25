# R3 CNEQUITY RUNTIME CONTRACT SYNC - V01 (author report)

DATE: 2026-08-25
BASE_HEAD: de1c64b011d9a3e488cb2cb732b31a3c8f8aaf1c

## SYNCHRONIZED CONSTANTS

```text
OLD_PINNED_CNEQUITY_SHA = a18ee0484dfb0801650175471724def3228b8a17
NEW_PINNED_CNEQUITY_SHA = ecf57023d57dcf925e9da3aa0e023492abb1221d

OLD_LOCK_SHA = 5f233fa9434624391c06e56a4596edfd52c1ec596d66688753b78f424dd571ac
NEW_LOCK_SHA = dc0e597ab9c2895cefa12dd0ae96297b1dde8c72852e211293b54036a8d9dbaf
```

NEW_LOCK_SHA is the SHA-256 of uv.lock exactly as committed at BASE_HEAD; the
lockfile was NOT regenerated in this task.

## MINIMAL PATCH

src/ashare_data/r3_daily.py changed only:

```text
PINNED_CNEQUITY_SHA -> ecf57023...
LOCK_SHA           -> dc0e597a...
```

No PLAN_SHA / BASE_HEAD / V08_SCOPE_DECISION_SHA / CONFIG_SHA / AS_OF /
provider semantics / daily semantics / callable contracts altered.

tests/test_r3_daily_foundation.py frozen-constants assertion updated because
it explicitly pins the old runtime authority and must track the frozen
contract (task 8 allowance).

## REFERENCE CLASSIFICATION

```text
RUNTIME_AUTHORITY        : r3_daily.py two constants + foundation test assertion (synchronized)
HISTORICAL_PROVENANCE    : R2 baseline / R4A0 / R4A gates still reference a18ee048
                           (frozen historical evidence; NOT rewritten)
STALE_CONTRACT_REFERENCE : none found
```

## RUNTIME PROVENANCE

```text
_runtime_pin_proof().verified = true
  version = 0.7.2
  commit  = ecf57023d57dcf925e9da3aa0e023492abb1221d
runtime_provenance PASS
direct_url commit matches ecf57023...
lock_sha256 == NEW_LOCK_SHA
```

## TARGETED TESTS

```text
test_r3_daily_foundation.py         219 passed (previously failing
  RUNTIME_CONTRACT_DRIFT tests now pass)
combined (foundation + R4A + audit) 337 passed
UNRELATED_FAILURES = none in targeted scope
```

## DECODER REGRESSION (in-runtime, no network)

```text
category=9  0x41800000 -> 16.0   (float32)
category=8  0x41800000 -> 2056.0 (BASE packed get_volume)
```

## SAFETY

```text
R3_MARKET_DATA_WRITE=NO
R3_DATA_REBUILD_EXECUTED=false
NETWORK_PROVIDER_DATA_FETCH=NO
R4A9_CHECKPOINT_MUTATED=false
R4A9_RESUME_AUTHORIZED=false
PRECLOSE_COMPLETE=false
```

AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
