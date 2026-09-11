# CNEquity local additive patches

These are **ASL-owned local patches** to the pinned CNEquity runtime. They are
not upstream releases and must never be presented as one.

```text
CNEQUITY_UPSTREAM_BASE = 0.8.0 @ d453853da766b3ba3e44489c0fb6e0089243fa25
UPSTREAM_PR            = not submitted
```

## Patch chain

| # | Patch | Scope |
|---|---|---|
| 0001 | `0001-valuation-metrics-turnover-rate.patch` | add `turnover_rate` (EastMoney `f8`) |
| 0002 | `0002-valuation-metrics-snapshot-date-guard.patch` | request `f124`; refuse a page that does not describe the requested session |

Both are applied in order to the installed package. The chain has been replayed
from a pristine 0.8.0 checkout and reproduces every touched file byte-for-byte
(`.venv`, `tests/test_snapshot_guard.py`, `tests/test_valuation_turnover_rate_v01.py`).

```text
cd /Users/luke808/ASL
patch -p1 -d .venv/lib/python3.12/site-packages < patches/cnequity/0001-valuation-metrics-turnover-rate.patch
patch -p1 -d .venv/lib/python3.12/site-packages < patches/cnequity/0002-valuation-metrics-snapshot-date-guard.patch
```

CNEquity is a git dependency resolved into `.venv`, not a checkout, so the
patches are applied to the installed package with their provenance recorded
here. Tests assert both are present in the running interpreter, so a re-install
that drops them fails the suite rather than silently degrading behaviour.

## 0001 - turnover_rate

Daily Facts needs a canonical `turnover_rate`. Deriving it locally as
`R3 volume / share_structure.float_shares * 100` produced 275 of 5198 keys
one-directionally larger than the certified value, because EastMoney's
share-structure report and the exchange's turnover denominator are different
bases for part of the market.

EastMoney's clist response already carries the exchange's own turnover rate in
field `f8`, on the same paginated bulk request CNEquity already makes. Adding it
costs no extra provider call and removes the local denominator entirely.

```text
schema change       additive: turnover_rate: Float64, nullable
primary key         unchanged (symbol, trade_date)
existing columns    unchanged in name, type and semantics
unit                PERCENT (f8 value 2.35 means 2.35%), matching the frozen
                    V1 turnover contract
null semantics      -, empty, junk, nan and inf stay null; a genuine 0 is kept
```

## 0002 - snapshot-date guard

CNEquity declares `valuation_metrics` as `fetch_semantics = snapshot`: the vendor
serves a live page stamped with the requested date, and a past date can never be
replayed. Two failure modes, both observed live:

* **Forged stamping.** Passing `trade_date=2026-09-09` while the page describes
  the 2026-09-10 close writes a partition that is simply false. This actually
  happened in an earlier round and was caught only by a later audit.
* **Intraday capture.** During the session the page keeps updating, so the values
  are partial and the session has not settled.

The guard makes a caller unable to *declare* a session. It resolves the session
the vendor's own payload describes, from `f124` (the per-row last-update epoch
requested on the same response, so no extra request), and requires it to match
the requested trade date from a settled window.

```text
new module       cnequity/domain/snapshot_guard.py
adapter change   _VALUATION_FIELDS += f124; guard runs before rows are returned
checks, in order SNAPSHOT_UNSTAMPED      no row carries an update epoch
                 SNAPSHOT_SESSION_MISMATCH  payload describes another session
                 SNAPSHOT_SESSION_INCOHERENT <90% of stamps share one session
                 SNAPSHOT_NOT_SETTLED     latest stamp is before 15:05 Asia/Shanghai
                 SNAPSHOT_WINDOW_NOT_OPEN local clock is before the settlement cutoff
                 SNAPSHOT_TURNOVER_ABSENT no row carries a turnover value
                 SNAPSHOT_TURNOVER_RESET  <50% non-zero: the next-session reset
cost             0 extra requests (f124 rides on the existing response)
```

The guard runs inside `fetch_valuation_metrics`, before anything reaches staging,
so a refusal cannot leave a partial partition behind. `require_settled=False`
relaxes only the settle requirement; the session-date check is not optional,
because writing values under a date they do not describe is simply incorrect
data rather than a policy choice.

Live behaviour observed on 2026-09-11 at 12:30 Asia/Shanghai:

```text
requested 2026-09-11 -> SNAPSHOT_NOT_SETTLED     (stamp 12:30:27, intraday)
requested 2026-09-10 -> SNAPSHOT_SESSION_MISMATCH (payload describes 09-11)
```

## Recorded hashes

sha256, first 16 hex digits, of the live files after the full chain:

```text
adapters/eastmoney/valuation.py  2593c8dabc87863f
domain/schemas.py                69bfa5380f3ab311
domain/datasets.py               cb4ed8999c2dbe33
domain/snapshot_guard.py         d783cb960b83f468
```

## Operational consequence

CNEquity's `validate_dataframe` requires every schema column to be present, so a
`valuation_metrics` partition written before 0001 cannot be validated after it.
Any pre-patch partition must be **rebuilt, not merely re-read**. In this lake that
was two partitions, both replaced within the authorized bounded rebuild; the
superseded artifacts are preserved under
`staging/valuation_metrics_quarantine_v01_pre_turnover/`.

## Contract vintage

`contracts_registry_after_patch.json` is the registry contract generated after
0001 and validates against the registry (`Contract OK`). Its diff against the
shipped `contracts/v0.8.0.json` is exactly one added column and one unit entry.

The shipped `contracts/v0.8.0.json` inside the package is deliberately **not**
rewritten: it documents the upstream 0.8.0 release, and editing it would forge an
upstream artifact.
