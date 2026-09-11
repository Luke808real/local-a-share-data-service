# Daily Facts V02 live rehearsal, 2026-09-11

## 0. Status

```text
STATUS = BLOCKED_SNAPSHOT_WINDOW
```

The task arrived at 12:26 Asia/Shanghai on a Friday, with the 2026-09-11 A-share
session still running (the afternoon session ends at 15:00 and CNEquity's own
settlement cutoff is 15:05). Per the task's section 3 this round therefore
completed every pre-close deliverable and deliberately did not fetch or commit a
valuation partition, and did not generate a 09-11 candidate.

```text
BASE_HEAD = 9afc81587bb71903fde2165865ae12e907486041
BRANCH    = codex/r3-incremental-hardening-daily-facts-phase1-v01
```

## 1. What the window actually was

Two independent guards confirmed the same answer, which is the point of having
both:

```text
local time                       2026-09-11 12:32 Asia/Shanghai
2026-09-11 is a trading day      yes (persisted trading_calendar)
CNEquity A_SHARE_FINAL_AT        15:05:00
R3 updater guard                 REJECTED
    "daily_bars 2026-09-11: the current A-share session is not final until
     15:05 Asia/Shanghai (now 12:26:30); refusing to stage an in-progress
     daily bar."
snapshot-window gate             BLOCKED_SNAPSHOT_WINDOW
```

The R3 refusal comes from upstream CNEquity, unmodified. It is worth recording
that R3's own dry-run reports `READY` while its execute path refuses: the dry-run
answers "is this date after the published authority", not "has this session
finished". A caller who trusted only the dry-run would have tried to publish a
half-formed session.

## 2. What was completed

```text
code inspection                     done
snapshot-date guard implementation  done (CNEquity patch 0002)
guard tests                         20 passed
R3 dry-run                          READY, network_request_n = 0
LocalQuery baseline                 verified
MCP baseline                        verified live over loopback
```

## 3. The snapshot-date guard

### 3.1 Why it had to exist

`valuation_metrics` is declared `fetch_semantics = snapshot`: the vendor serves a
live page stamped with whatever date the caller asks for. An earlier round passed
`trade_date=2026-09-09` and got back a page describing the 2026-09-10 close -
two partitions, byte-identical, one of them labelled with a session it did not
describe. That was caught by a later audit, not by the write path.

The guard closes that at the acquisition boundary rather than in review. A caller
can no longer *declare* a session: the guard resolves the session from the
vendor's own payload and requires it to be the requested one.

### 3.2 Mechanism

`f124`, the per-row last-update epoch, was added to the adapter's field list. It
rides on the same paginated response that already carried `f8`, so the guard
costs **zero extra requests**. Validation runs inside `fetch_valuation_metrics`
before any row is returned, so a refusal cannot leave a partial partition.

```text
SNAPSHOT_UNSTAMPED          no row carries an update epoch
SNAPSHOT_SESSION_MISMATCH   the payload describes a different session
SNAPSHOT_SESSION_INCOHERENT fewer than 90% of stamps share one session
SNAPSHOT_NOT_SETTLED        the latest stamp is before 15:05 Asia/Shanghai
SNAPSHOT_WINDOW_NOT_OPEN    the local clock is before the settlement cutoff
SNAPSHOT_TURNOVER_ABSENT    no row carries a turnover value
SNAPSHOT_TURNOVER_RESET     fewer than 50% non-zero (the next-session reset)
```

The session is taken from the **latest** stamp, not the modal one: a page mixes
long-halted rows carrying an old stamp with live ones, and only the newest stamp
says how far the page has advanced. `require_settled=False` relaxes only the
settle requirement; the session-date check is not optional, because writing
values under a date they do not describe is incorrect data rather than a policy
choice.

### 3.3 Live proof, not just synthetic

Run against the real endpoint at 12:30 Asia/Shanghai:

```text
requested 2026-09-11 -> SNAPSHOT_NOT_SETTLED
    latest vendor stamp 2026-09-11 12:30:27, session not final before 15:05
requested 2026-09-10 -> SNAPSHOT_SESSION_MISMATCH
    "vendor payload describes 2026-09-11, not the requested 2026-09-10"
```

The second line is the earlier round's bug, now structurally impossible: the
exact call that used to produce a forged partition now refuses.

### 3.4 A discovery that shaped the guard

An early guard design would have checked only the stamp's *date*. Live capture
at 12:26 showed why that is not enough: during the session `f124` tracks **live
ticks** (12:26:00, 12:26:03, ... 12:27:06), so the date is right while the
session is incomplete. The date check alone would have passed an intraday
capture. The settle check is what makes the guard meaningful during market
hours, and it is the check that fired in this round's rehearsal.

## 4. Rehearsal result

```text
stage  preflight   trading_day  true
                  local_now    2026-09-11 12:32:33 Asia/Shanghai
                  provider_snapshot_timestamp    2026-09-11T12:33:00+08:00
                  resolved_snapshot_trade_date   2026-09-11
                  session_coverage               1.0  (5913 of 5913 stamped)
                  turnover_zero_n                363 of 5913  (6.1%, normal intraday)
                  is_settled                     false
                  local_window_open              false
                  would_pass                     false
                  status                        BLOCKED_SNAPSHOT_WINDOW
stage  r3                 NOT REACHED
stage  trading_status     NOT REACHED
stage  valuation_metrics  NOT REACHED
stage  v02_derivation     NOT REACHED
```

Note what the probe did and did not establish. `resolved_snapshot_trade_date`
**did** match the target - so the snapshot was not stale or future-stamped. The
block is purely that the session had not finished, which is the correct reason
and a different failure from the one the previous round hit. Reporting these
separately is the point of resolving the session rather than only comparing
dates.

## 5. Provider request accounting

```text
this round, rehearsal
  valuation probe (1 clist walk, 5913 rows sampled)   61 page requests
  R3                                                  0  (not run)
  TDX                                                 0
  BAOSTOCK                                            0
  official disclosure                                 0
  PER_SYMBOL_REQUEST_N                                0
```

Separately, developing the guard required read-only diagnostic walks against the
live endpoint (the guard probe above, an `f124` semantics probe, and two
`fetch_valuation_metrics` attempts). Those are development diagnostics, not part
of the rehearsal's own budget, and every one of them was read-only: no partition
was written by any of them, which section 7 verifies.

## 6. Fail-closed verification

The rehearsal stopped at the gate, so the important check is what it did *not*
leave behind.

```text
curated/valuation_metrics partitions      0
staging/valuation_metrics files           0
new CNEquity runs this round              none
R3 pointer                                140197cd... (unchanged)
R3 file_n                                 2598
R3 max date                               2026-09-10
2026-09-11 R3 partition                    absent
Facts V1 pointer                          9b4f474e... (unchanged)
V02 pointer                               none created
Daily Facts V1 values read by the candidate generator   none
```

The last engine runs in `meta/manifest.db` predate this round (00:49-00:50 UTC,
the previous round's valuation work), which confirms the rehearsal never reached
the engine. A leftover staging file from that earlier run held the known-wrong
pre-open zeros; it was moved into the existing quarantine rather than deleted, so
a future retry cannot compact it.

## 7. Baselines

```text
LocalQuery  DAILY_PUBLISHED_AS_OF  2026-09-10
            DAILY_MANIFEST_FILE_N  2598
            DAILY_MANIFEST_HASH    140197cd3c95a3f8e1c9f5750f117c66eafc09d9113757e511f31e46848bf66b
            DAILY_USABLE           true
MCP (live)  LATEST_PUBLISHED_TRADE_DATE  2026-09-10
            DAILY_MANIFEST_HASH          140197cd...
            FACTS_READY                  false
```

## 8. Patch provenance

The guard is CNEquity patch **0002**, applied on top of patch **0001**
(turnover_rate). The chain was replayed from a pristine 0.8.0 copy and reproduces
every touched file byte-for-byte:

```text
adapters/eastmoney/valuation.py  replayed 2593c8dabc87863f  live MATCH
domain/schemas.py                replayed 69bfa5380f3ab311  live MATCH
domain/datasets.py               replayed cb4ed8999c2dbe33  live MATCH
domain/snapshot_guard.py         replayed d783cb960b83f468  live MATCH
```

Both patches remain ASL-local; no upstream PR was opened, and the shipped
`contracts/v0.8.0.json` was not rewritten.

## 9. What 09-11 still needs

Nothing about the remaining chain is unproven except its timing. After the 15:05
cutoff, the same command should run to completion:

```text
.venv/bin/python tools/run_daily_facts_v02_live_v01.py --trade-date 2026-09-11 --execute
```

and it will then evaluate R3, `trading_status`, `valuation_metrics`, the V02
derivation and certification in one pass. Three things are expected to be
different from the blocked run and are worth watching:

1. `is_settled` becomes true and `local_window_open` becomes true.
2. The valuation adapter's own guard re-validates on the full fetch, so a
   snapshot that degrades between the probe and the fetch is still refused.
3. The turnover cross-check becomes possible for the first time on a live date -
   09-11 has no V1 golden answer, so section 14 of the task's crosscheck plan
   (R3 volume, `float_mv`/price consistency, and R3 zero-bar versus CNEquity
   suspension) is the only evidence available.

## 10. Tests

```text
.venv     : 206 passed, 1 failed (pre-existing untracked file, unchanged)
.venv-mcp : 88 passed
new       : tests/test_snapshot_guard.py - 20 passed
py_compile: passed
cne config validate : Configuration OK
cne contract validate : Contract OK
```

Guard coverage: session resolution from the vendor stamp; raw vendor field names
accepted alongside adapter names; the latest stamp winning over a stale majority;
coverage measured on date rather than minute; unstamped, wrong-session,
future-session, intraday, pre-cutoff, whole-market-zero, just-past-threshold,
incoherent and absent-turnover payloads each refused with their own code; the
settled happy path; a legitimate suspended zero not tripping the reset gate;
`require_settled=False` still enforcing the date; and the adapter itself
exercised through its real code path with a stubbed page walker for both the
refusal and the success case.

## 11. Known limitations

```text
1. The live chain past the preflight gate is still unexercised for 2026-09-11.
   R3, trading_status, valuation and the V02 derivation did not run this round,
   so RUNTIME_* figures for them are unmeasured for this date.
2. The rehearsal cannot be re-run meaningfully before 15:05; it will report the
   same blocked status, which is correct rather than a defect.
3. The guard's settle cutoff (15:05) is inherited from CNEquity's daily-bar
   cutoff rather than measured against when EastMoney actually finalises the
   page. The 09-10 page settled by 15:34, so 15:05 is reachable, but the true
   earliest-safe instant is not pinned. A 15:05 fetch is accepted by the guard
   and may still be a few minutes early.
4. Whether a pre-cutoff refresh can later overwrite the same partition correctly
   is untested; the daily path is expected to run once, after close.
5. tests/test_cnequity_v080_daily_reuse.py still fails; it is untracked, was not
   authored here, and is unrelated to this work.
```

## 12. Next gate

```text
NEXT_GATE = re-run the live rehearsal after 15:05 Asia/Shanghai on 2026-09-11,
            then the separate authority-migration gate

            Daily Facts V02 production authority migration
            + 09-10 bridge decision
            + 09-11 first formal V02 publication
```

No Daily Facts V02 authority was created, no V1 pointer was replaced, and no
2026-09-11 Facts were published.
