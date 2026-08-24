# R3 DAILY FALLBACK UNIT CONTRACT CLOSURE - V01 (author report)

DATE: 2026-08-24
BASE_HEAD: 40bd2411d5dbebcc8a8e551c4a6edea655100b89

## BAOSTOCK NORMALIZATION (pinned CNEquity a18ee0484..., 
cnequity.adapters.baostock.delisted_bars)

BAOSTOCK_RAW_VOLUME_UNIT=baostock documented shares; pinned adapter passes
volume through as finite_int64 with NO multiplier.
BAOSTOCK_CANONICAL_VOLUME_UNIT=daily_bars data_version=v2 (shares).
BAOSTOCK_VOLUME_TRANSFORM=NONE (guard only).

## TDX RAW -> R3 CANONICAL TRACE

TDX adapter raw == staging == curated (batch-139) for 2016-09-28/09-30/10-11;
the previously reported 11200/205600/22400 are the raw adapter values and
the canonical R3 values (no transform).

## FULL OHLC PARITY (overlap dates)

2016-09-28  O/H/L/C diff = 0.0/0.0/0.0/0.0  EXACT
2016-09-30  O/H/L/C diff = 0.0/0.0/0.0/0.0  EXACT
2016-10-11  O/H/L/C diff = 0.0/0.0/0.0/0.0  EXACT

## AMOUNT PARITY

amount equal on all three overlap dates (105179 / 56960 / 189552).

## VOLUME CONTRACT (critical)

TDX volume vs BaoStock volume pairs:
  2016-09-28  11200 vs 3700   ratio 3.027
  2016-09-30  205600 vs 1600  ratio 128.5
  2016-10-11  22400 vs 4400   ratio 5.091

Ratios are NOT a constant multiplier -> no exact volume normalization rule
can be derived from overlap evidence.

VOLUME_CONTRACT_PASS=false
AMOUNT_CONTRACT_PASS=true

OBSERVABILITY_CLASSIFICATION=TDX_FIELD_SEMANTIC_DIFFERENCE
(volume semantics differ between TDX and BaoStock; amount parity proven,
volume parity not)

## FALLBACK ROW SIMULATION (in memory, no write)

Both candidate rows pass row-level QA (finite, volume>=0, OHLC consistent,
close>0) using pinned normalizer semantics. NO file written.

## NO-CONFLICT GATE

2016-09-29: TDX canonical absent=true, BaoStock present=true, expected
traded=true -> FALLBACK_ELIGIBILITY=KEY_OK_VOLUME_UNRESOLVED
2016-10-10: same -> FALLBACK_ELIGIBILITY=KEY_OK_VOLUME_UNRESOLVED

## DECISIONS

FALLBACK_UNIT_CONTRACT_DECISION=FALLBACK_UNIT_CONTRACT_FIX_REQUIRED
REPAIR_AUTHORITY_DECISION=NO_REPAIR_YET

OHLC/amount compatibility and no-conflict eligibility are proven, but the
volume semantics are UNRESOLVED (raw/canonical unit and exact rule unknown;
observed ratios are not constant). Repair cannot proceed until the volume
contract is resolved.

## SAFETY

R3_MARKET_DATA_WRITE=NO
R4A9_CHECKPOINT_MUTATED=false
R4A9_RESUME_AUTHORIZED=false
PRECLOSE_COMPLETE=false

AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT
