# R3 DAILY FOUNDATION IMPLEMENTATION PLAN — V07.1 (PROPOSED REVISION, CORRECTED)

**STATUS:** `PENDING_GPT_5_6_SOL_AUDIT — V07.1` — author-drafted under Sol
decision `approve-v07-revision`, then corrected per task
`R3_V07_1_PLAN_CORRECTION`. NOT executable. The frozen plan
`R3_DAILY_FOUNDATION_IMPLEMENTATION_PLAN.md` at SHA
`d13e2ecefbb66250b73aca4312dc8706a4d2b7a3` remains authoritative until this V07
revision passes an independent plan audit.
**BASE_HEAD:** `0254122a99f0a365d2be12f29a2a59b951497fd3`
**SPEC_VERSION:** `V1.0 FROZEN` (unchanged)
**SUPERSEDES / KEEPS:** V07 revises ONLY the security-universe discovery and
BJ daily route contracts. It does not change MASTER_SPEC, DECISIONS, R3 data
semantics (RAW daily, units, PK, provenance, AS_OF), the DAILY_READY quality
threshold, or legacy policy.

## 1. Scope of this revision (Sol decision `approve-v07-revision`)

Goal: remove exhaustive Sina code-space discovery as a hard single-point
dependency **only where equivalent or stronger all-A / survivorship guarantees
can be proven**, preserving tri-state semantics and fail-closed behavior.

Constraints honored:
- No changes to MASTER_SPEC, DECISIONS, R3 data semantics, DAILY_READY quality
  threshold, AS_OF semantics, or legacy policy.
- No additional market-data stages executed during this task.
- Stage A, instruments (7757), formal delisted (337), partial discovery
  catalog, ledger, blocker/pacing/recovery evidence, staging and curated data
  are preserved exactly. No cleanup, no restart.
- Sina is treated as `SOURCE_FRAGILE`, not `SOURCE_RELIABLY_RECOVERED`; no
  frozen full-code-space sweep is resumed under this revision.

## 2. Identity contract actually required by R3 (re-evaluation)

| Letter | Identity need | Required by frozen R3 / MASTER_SPEC | Evidence |
|---:|---|---|---|
| A | Current SH/SZ universe | Yes (all current SH/SZ stocks incl. CDR) | MASTER_SPEC §5, D006; R3 Stage A (done) |
| B | Historical SH/SZ delisted identity | Yes (delisted securities must resolve; D006, §5, gate #2) | MASTER_SPEC §5, §7.1, §63 gate 2, D006 |
| C | Current BJ universe | Yes (BSE part of all-A; no board-dependence deletion) | MASTER_SPEC §5, D006; R3 Stage C2 |
| D | Historical BJ delisted identity | Implied by "no silent survivorship omission"; not BJ-specific in spec | MASTER_SPEC §5 / D006 (general delisted) |
| E | No silent survivorship omission | Yes (spec forbids dropping delisted facts) | MASTER_SPEC §5, D006, §20 Tier0/1/3 |
| F | Explicit UNKNOWN / SOURCE_ERROR semantics | Yes (fail-closed; no SOURCE_ERROR→NOT_FOUND) | AGENTS.md fail-closed; R3 contract tri-state |

MASTER_SPEC does **not** require enumerating codes that never existed
(`never_issued`). It requires that every currently listed and every historical
delisted security be present and resolvable, without silently omitting any.

## 3. Candidate authorities from already-pinned CNEquity v0.7.2

| Need | Candidate pinned authority | Evidence |
|---|---|---|
| A SH/SZ current | TDX live security list + EastMoney clist (Stage A already done; TDX+baostock sources present) | `steps/reference.py:54-90`; Stage A result 7757 rows |
| B SH/SZ historical | Baostock `query_stock_basic` (listed+delisted identity) + per-day `query_all_stock` rosters + `fetch_delisted_bars` | `adapters/baostock/instruments.py:88-131`, `adapters/baostock/delisted_bars.py:41-95,1240+` |
| C BJ current | EastMoney clist `f12/f13/f14/f26` (f13=2→.BJ) | `adapters/eastmoney/common.py:10,117-134`; used by Stage C2 |
| D BJ historical | **None pinned** — EM clist is current-only; TDX has no BJ; Baostock `_is_stock` rejects BJ | `adapters/baostock/delisted_bars.py:41-57`; `adapters/tdx_protocol/minute_bars.py:216-219` |
| E survivorship (SH/SZ) | Baostock rosters give the positive traded set per trading day; closed over R3 window | `delisted_bars.roster_on` / `steps/delisted.py` `_delisted_universe` |
| F tri-state | All adapters above: EXISTS / NOT_EXISTS / SOURCE_ERROR; failures must not set NOT_FOUND/NOT_EXISTS | pinned adapter code; V07 contract below |

### 3a. Baostock authority split and roster-closure receipt (V07.1 correction)

Authority is split explicitly:

```text
Baostock stock_basic (query_stock_basic)
  = SH/SZ FORMAL HISTORICAL IDENTITY AUTHORITY
    (listed + delisted names, list_date/delist_date)

roster_on (query_all_stock per day)
  = CLOSURE / RECONCILIATION EVIDENCE only
```

Any claim that the SH/SZ universe is `roster_closed` requires a receipt:

```text
expected_dates_n
success_dates_n
failed_dates_n
union_symbol_n
union_symbol_hash
stock_basic_vs_roster_diff
unresolved_n
```

And the fail-closed rule:

```text
failed_dates_n > 0  =>  NOT CLOSED
```

`NOT CLOSED` means the SH/SZ historical delisted set cannot be certified on that
evidence and must be surfaced as explicit residual, never silently treated as
complete.

## 4. Dependency graphs

### Old (frozen) R3 discovery graph

```text
Stage B discovery:
  issued_code_space() ──probe──> Sina quotes_service getKLineData
      ├─ 200 + data ───────────────> delisted (last trade date)
      ├─ 200 + empty ──────────────> never_issued
      └─ SOURCE_ERROR (456/5xx) ──> pending (re-probe)  [HARD gate: remaining=0]

Stage E delisted daily:   SH/SZ -> Baostock fetch_delisted_bars
                          BJ    -> Sina fetch_daily_bars_sina     (hard dep)
Stage F2 BJ daily:        BJ    -> Sina fetch_bars_via_sina (primary)
                                     └ EastMoney kline (gap fallback only)
```

### Proposed V07 discovery graph

```text
Stage B (V07) identity completion — no Sina hard dep:
  SH/SZ current ..: Stage A (done) — TDX + EastMoney (unchanged)
  SH/SZ historical: Baostock stock_basic (delisted identity)
                  + per-day rosters (positive traded universe, closed)
                  -> delisted set proven positively (no never_issued needed)
  BJ current ......: EastMoney clist f12/f13/f14/f26 (Stage C2)
  BJ historical ...: research step (bounded, pinned-only)
                     -> if proven: add via that pinned source
                     -> else: UNKNOWN_CARRIED (explicit, hashed, fail-closed)
  Sina sweep ......: downgraded HARD_GATE -> OPTIONAL_CROSSCHECK (supplementary)
                     partial catalog preserved as crosscheck evidence only

Stage E delisted daily (V07):  SH/SZ -> Baostock fetch_delisted_bars
                               BJ    -> EastMoney push2his kline (no Sina)
Stage F2 BJ daily (V07):       BJ -> EastMoney push2his kline (primary)
                                    (Sina optional crosscheck; not required)
```

## 5. Tri-state semantics (all authorities)

Every identity/probe step must return and persist exactly:

```text
EXISTS          -> positive identity / bar evidence
NOT_EXISTS      -> proven absence (empty response from a source that
                   distinguishes empty from error, e.g. Sina empty==never issued)
SOURCE_ERROR    -> transport/auth/parse failure -> NEVER becomes NOT_EXISTS
```

- `SOURCE_ERROR` is recorded, retried only per exact-scope rules, and never
  mapped to `NOT_EXISTS` / `NEVER_ISSUED`.
- Baostock failures raise (fail-loud, as pinned) and must surface as
  `SOURCE_ERROR`, not as a closed roster.
- EastMoney clist pagination already fails loud on truncation
  (`eastmoney/clist.py`) — preserved.
- BJ historical: if the pinned-only research step cannot produce positive
  identity (see §7), the bucket is `HISTORICAL_DELISTED_BJ = UNKNOWN_CARRIED`
  — counted, hashed, never silently excluded from reporting, and it explicitly
  prevents claiming full all-A survivorship completeness for BJ.

### 5a. EastMoney tri-state — thin service-owned wrapper (V07.2 correction)

Pinned `eastmoney.bars.fetch_daily_bars()` must NOT be used directly as the
authoritative tri-state fetcher. The plan defines a thin service-owned wrapper
that returns EXACTLY one of three states:

```text
EXISTS
NOT_EXISTS
SOURCE_ERROR
```

Mapping for a BJ symbol already confirmed in the security master:

```text
valid bars                             -> EXISTS
HTTP / transport / parse failure       -> SOURCE_ERROR
empty response                         -> SOURCE_ERROR (reason=EMPTY_KNOWN_SYMBOL)
invalid payload / non-bar payload      -> SOURCE_ERROR (reason=INVALID_KNOWN_SYMBOL_RESPONSE)
```

An empty `push2his` response for a known BJ symbol NEVER becomes `NOT_EXISTS`.
`NOT_EXISTS` is reserved exclusively for a protocol/source that can positively
prove identity absence (the wrapper never returns it on its own for a known
symbol).

### 5b. Coverage layer (V07.2) — separate enum from the wrapper

The wrapper state and the R3 coverage classification are two distinct layers
and their enums are never mixed:

```text
provider wrapper:
  EXISTS / NOT_EXISTS / SOURCE_ERROR

coverage classifier (only after exact retries / fallback / reconciliation):
  OBSERVED
  EXPLAINED_MISSING
  UNEXPLAINED_MISSING
  PENDING_R4_STATUS_EXPLANATION
```

`UNEXPLAINED_MISSING` is produced only by the coverage classifier after exact
scoped retries / fallback / reconciliation are exhausted — never by the wrapper
itself.

## 6. DAILY_READY behavior (V07.1 frozen)

The DAILY_READY quality threshold is **unchanged**. V07 changes the *inputs*
that must substantiate it, and the `UNKNOWN_CARRIED` bucketing is now frozen
with explicit phase gating:

- SH/SZ: roster-closed positive coverage (equivalent or stronger than the Sina
  sweep) — retains the concrete sanity checks (daily_duplicate=0, date bounds,
  value checks, unit checks, per-active-symbol positive-volume coverage).
- BJ current: EM-clist identity completeness (unchanged C2 gate).
- `HISTORICAL_DELISTED_BJ = UNKNOWN_CARRIED` may exist as an R3 intermediate
  state, but it freezes the phase:

```text
DAILY_READY = FALSE
R3_EXIT      = BLOCKED_BJ_HISTORICAL_IDENTITY
R4_EXECUTION = FORBIDDEN
```

  until BOTH:

```text
BJ_HISTORICAL_AUTHORITY      = PROVEN
BJ_HISTORICAL_UNRESOLVED_N   = 0
```

The `UNKNOWN_CARRIED` bucket is counted and hashed, is never counted as
EXPLAINED, and must be surfaced in the author report, verifier output, and
PROJECT_STATE carry-forward. R3 never claims BJ-survivorship-complete while it
is non-empty.

## 6a. Signed carry-forward (supersedes the earlier V07 §6 wording)

See §6 above for the frozen `DAILY_READY = FALSE` /
`R3_EXIT = BLOCKED_BJ_HISTORICAL_IDENTITY` / `R4_EXECUTION = FORBIDDEN` gate and
the `PROVEN` + `BJ_HISTORICAL_UNRESOLVED_N = 0` release conditions.

## 7. Bounded research step (identity proof, not data collection)

BEFORE any data stage resumes, complete a read-only investigation to attempt
positive BJ historical delisted identity. Candidates in scope:

### 7a. Pinned-source candidates (already-pinned CNEquity v0.7.2)

1. EM datacenter endpoints available from pinned `eastmoney` adapters (e.g.,
   `datacenter-web.eastmoney.com`) for delisted/status fields on BJ codes.
2. Whether Baostock any API exposes BJ (it does not per `_is_stock`), and
   whether any pinned adapter vendor catalog carries BJ delisted names.
3. Deterministic cross-check of current BJ list coverage between EM clist and
   any secondary pinned source.

### 7b. BSE official public-source candidates (bounded research, V07.1)

Add the Beijing Stock Exchange official public channel as a research candidate:

- Whether an enumerable directory/announcement index of historical
  termination-of-listing (终止上市/退市) securities exists;
- Whether **completeness** can be established, not merely a single announcement;
- symbol / name / list_date / delist_date fields;
- `920xxx` new vs legacy code mapping;
- provenance / pagination / historical coverage semantics.

Known sanity case: `920305` (南京云创大数据科技股份有限公司; BSE has published
a termination-of-listing decision). The 2026-08-18 bounded probe confirmed the
public symbol/name mapping `920305 -> 云创退` via EastMoney quote, as
supplementary evidence only. Finding one case never establishes completeness.

### 7c. Bounded probe evidence (2026-08-18, read-only)

Direct, proxy-cleared GETs against `https://www.bse.cn`:

| URL | Result |
|---|---|
| `/` | 200 — WAF JS cookie challenge (bootstrap only, sets 300s `C3VK` cookie, `window.open("/","_self")`) |
| `/disclosure/` | 404 |
| `/about/base/organization/` | 404 |
| `/data/stock/stockdirectory/` | 404 |
| `/stock/announcement/` | 404 |
| `/disclosure/more/` | 404 |
| `/sitemap.xml` | 403 |

The official site is a WAF/JS-challenge SPA; a bounded read-only pass did not
surface an enumerable, completeness-provable delisted-security catalog or
announcement index. Public web search was unavailable (platform 429). Web
quality indicates no completeness claim may be made from the one confirmed
case.

### 7d. Verdict

```text
BJ_HISTORICAL_AUTHORITY = UNPROVABLE_BOUNDED_RESEARCH
```

`PROVEN_BSE_OFFICIAL` is NOT established because an enumerable, complete,
official delisted-catalog cannot be demonstrated under the bounded read-only
research performed. Therefore the plan proceeds with
`HISTORICAL_DELISTED_BJ = UNKNOWN_CARRIED` and the frozen
`DAILY_READY = FALSE` / `R3_EXIT = BLOCKED_BJ_HISTORICAL_IDENTITY` /
`R4_EXECUTION = FORBIDDEN` gate until a source proves BJ delisted history
completeness (`PROVEN` and `BJ_HISTORICAL_UNRESOLVED_N = 0`).

## 8. Resume point and execution delta (when audit passes)

Do NOT restart. Resume from completed Stage A, then execute the V07-adapted
sequence:

| Step | V07 behavior |
|---|---|
| Stage A | Kept (done, 7757 rows / 337 delisted) |
| Stage B (V07.1) | Baostock `stock_basic` = SH/SZ formal identity authority; `roster_on` = closure evidence with receipt (`failed_dates_n>0 => NOT CLOSED`); EM clist current BJ; BJ historical = UNPROVABLE_BOUNDED_RESEARCH (7c/7d) => UNKNOWN_CARRIED; Sina sweep optional crosscheck only; tri-state ledger |
| Stage C | unchanged (merge) |
| Stage C2 | unchanged (BJ metadata via EM f12/f13/f14/f26) |
| Stage D | unchanged (trading_calendar, TDX) |
| Stage E (V07.1) | SH/SZ via Baostock `fetch_delisted_bars`; BJ via EM push2his kline through the §5a tri-state wrapper; no Sina hard dep |
| Stage F (V07.1) | F1 SH/SZ via TDX parallel (unchanged); F2 BJ via EM push2his primary through the §5a tri-state wrapper (Sina crosscheck optional); unique batch ids; effective-span and zero-volume rules unchanged |
| Stage G | unchanged (delisted coverage report) |
| Quality | unchanged L0/L1/universe/gap gates + new `HISTORICAL_DELISTED_BJ` bucket; `DAILY_READY = FALSE` until `BJ_HISTORICAL_AUTHORITY = PROVEN` and `BJ_HISTORICAL_UNRESOLVED_N = 0` (§6) |

The controller changes for the V07.1 delta are **not implemented in this task**;
they will be implemented only after the V07.1 plan audit passes.

## 9. Exclusions and prohibitions (V07)

- No change to MASTER_SPEC, DECISIONS, DAILY_READY threshold, AS_OF, legacy
  policy, RAW-price/unit/PK/provenance semantics.
- No SINa full-code-space sweep; no repeated Sina probing.
- No new external dependency.
- No cleanup, delete, move, repair, compact, restart, or rewrite of preserved
  R3 evidence/staging/curated data.
- No market-data execution during this planning task.

## 10. Remaining blockers

1. This V07.1 plan requires an independent GPT-5.6 Sol audit before execution.
2. BJ historical delisted identity: `UNPROVABLE_BOUNDED_RESEARCH` (official
   BSE catalog not enumerable/provable under bounded read-only research;
   §7). `HISTORICAL_DELISTED_BJ = UNKNOWN_CARRIED` freezes
   `DAILY_READY = FALSE` / `R3_EXIT = BLOCKED_BJ_HISTORICAL_IDENTITY` /
   `R4_EXECUTION = FORBIDDEN` until `PROVEN` + `unresolved_n = 0`.
3. Sina remains `SOURCE_FRAGILE`; used only as optional crosscheck, never as a
   hard gate.

## 11. Deliverables of this task (committed only)

- `docs/plans/R3_DAILY_FOUNDATION_IMPLEMENTATION_PLAN_V07.md` (this file)
- `reports/planning/R3_V07_ALTERNATIVE_UNIVERSE_DISCOVERY_FEASIBILITY.md`
  (updated with the Sol-decision §5 evidence bullets)
