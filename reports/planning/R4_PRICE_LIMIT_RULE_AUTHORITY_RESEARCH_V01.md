# R4 PRICE-LIMIT RULE AUTHORITY RESEARCH — V01.2

STATUS: AUTHOR_ONLY_PENDING_SOL_REAUDIT
VERSION: V01.2 (binding of Sol-verified official anchors; no new research)
RESEARCH_DATE: 2026-08-19
SCOPE: SH/SZ price-limit basis for R4 stable_market_facts, window
2016-01-01..2026-08-17. BJ recorded for completeness in the rule table only;
BJ_EXTENSION = DEFERRED and no BSE research is carried in V01.1.

This report records the official rule basis used by the proposed R4D rule
engine. It is a research/planning artifact. It implements no code and modifies
no market data.

## 1. Source policy (V01.1)

Authority is ONLY:

- sse.com.cn
- szse.cn
- csrc.gov.cn

All secondary-source authorities (broker notices, news portals) are removed.
URLs below were captured during bounded official-website research. Where a
specific official document number/URL could not be re-verified in this pass,
it is explicitly marked REBIND_AT_R4D — the rule content is frozen per the
V01.1 contract, and the exact official citation must be re-pinned during R4D
implementation verification. No authority is fabricated.

V01.2 citation binding: three official anchors independently verified by Sol
are bound below, replacing the respective REBIND_AT_R4D markers:
深证会〔2014〕54号 (SZ pre-2023 main-board IPO valid order range), 深证上〔2026〕551号
and 上证发〔2026〕41号 (SSE/SZSE 2026 Trading Rules, effective 2026-07-06).
No new rule research was performed in V01.2 and no secondary authority is
introduced (SECONDARY_AUTHORITY_SOURCES_N = 0).

## 2. Rule table (SH/SZ, window 2016-01-01 .. 2026-08-17)

| Segment | Period (trading effective) | Limit | Authority |
|---|---|---|---|
| SH/SZ main board normal stocks | full window | 10% | SSE/SZSE Trading Rules |
| SH/SZ main board risk-warning (ST / *ST) | until 2026-07-05 | 5% | SSE/SZSE pre-2026 rules |
| SH/SZ main board risk-warning (ST / *ST) | from 2026-07-06 | 10% | SSE 上证发〔2026〕41号 / SZSE 深证上〔2026〕551号 (2026 Trading Rules, effective 2026-07-06; Sol-verified) |
| ChiNext 300xxx normal stocks | until 2020-08-21 | 10% | Pre-reform SZSE ChiNext rules |
| ChiNext 300xxx normal stocks | from 2020-08-24 | 20% | SZSE ChiNext Special Provisions (2020-08-24 effective) |
| ChiNext risk-warning | until 2020-08-21 | 5% | Pre-reform SZSE ChiNext risk-warning rule |
| ChiNext risk-warning | from 2020-08-24 | 20% | SZSE 深证上〔2020〕620号 implementation |
| STAR 688xxx | from 2019-07-22 | 20% | SSE STAR Trading Rules |
| STAR CDR 689xxx | from 2019-07-22 | 20% | SSE STAR Trading Rules (CDR in board scope) |
| STAR/ChiNext delisting-arrangement | after first day | 20% | SSE/SZSE rules |
| Main board delisting-arrangement | after first day | 10% | SSE/SZSE rules |
| IPO first 5 trading days (STAR/ChiNext) | from board launch | NO LIMIT | SSE/SZSE rules |
| IPO first 5 trading days (main board) | from 2023-04-10 | NO LIMIT | CSRC full-registration reform |
| Main board IPO first day (pre-2023) | until 2023-04-07 | PRICE_LIMIT=NONE; valid order range edge | SZ frozen per 深证会〔2014〕54号 (Sol-verified); SH open (OPEN_NONBLOCKING_EDGE: SH_PRE_2023_IPO_ORDER_RANGE_OPEN) |
| BSE | (recorded for completeness only) | 30%; first day no limit | BJ deferred; no BSE source carried in V01.1 |

## 3. Effective dates and official sources

### 3.1 STAR board (科创板) — 20% from 2019-07-22

- SSE investor education (科创板投教): STAR price limit is 20%; IPO stocks
  have no limit for the first 5 trading days.
  - http://edu.sse.com.cn/tib/ysptj/c/4768085.shtml

### 3.2 ChiNext (创业板) — 20% from 2020-08-24

- SZSE ChiNext Special Provisions, Rule 2.1: 20% price limit; IPO stocks no
  limit for the first 5 trading days.
  - http://www.szse.cn/lawrules/rule/repeal/rules/P020231230545310237980.pdf
  (historical rule text; URL path marks the provision as later superseded —
  keep as historical evidence, not current authority)
- SZSE notice on ChiNext risk-warning and delisting-arrangement trading:
  after the Special Provisions take effect, risk-warning and
  delisting-arrangement stocks trade at 20%.
  - http://www.szse.cn/disclosure/notice/general/t20200710_579459.html
- SZSE implementation evidence confirming 2020-08-24 effective date
  (ChiNext-traded funds list effective 2020-08-24).
  - https://docs.static.szse.cn/www/aboutus/trends/conference/W020200821767425488573.pdf
- ChiNext reform transition notice: 深证上〔2020〕620号 (implementation
  notice confirming 2020-08-24 as 业务实施日). REBIND_AT_R4D — exact page
  URL to be re-pinned during R4D verification.

### 3.3 Main board full registration — IPO first 5 days no limit from 2023-04-10

- CSRC Q&A on full registration reform: main board IPO stocks have no price
  limit for the first 5 trading days; from the 6th day the limit remains 10%.
  - http://www.csrc.gov.cn/jiangsu/c105409/c7176801/content.shtml
  - http://www.csrc.gov.cn/csrc/c100028/c7047624/content.shtml
- CSRC Shanghai: first batch of main-board registration IPOs listed on
  2023-04-10.
  - http://www.csrc.gov.cn/shanghai/c105566/c7402810/content.shtml

### 3.4 Main board risk-warning (ST / *ST) — 5% -> 10% from 2026-07-06

- SSE revision process: draft notice adjusting main-board risk-warning stock
  price limit from 5% to 10%, later incorporated into the 2026 Trading Rules
  revision.
  - https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260410_10814807.shtml
- SSE technical notice requiring front-end price checks at the 10% limit for
  main-board risk-warning stocks (implementation evidence).
  - https://www.sse.com.cn/services/tradingtech/notice/c/10783253/files/27d16cba23f847f6a9c9dd7320e5b20c.pdf
- Effective date frozen: 2026-07-06.
- SZSE 2026 Trading Rules — 深证上〔2026〕551号, effective 2026-07-06
  (Sol-verified authority):
  https://www.szse.cn/lawrules/rule/trade/current/t20260424_620190.html
- SSE 2026 Trading Rules — 上证发〔2026〕41号, effective 2026-07-06
  (Sol-verified authority):
  https://www.sse.com.cn/lawandrules/sselawsrules2025/trade/universal/c/c_20260424_10816492.shtml

### 3.5 Delisting arrangement (退市整理期)

- Main board: first trading day has no price limit; remaining days 10%.
  - SSE termination announcement example:
    http://www.sse.com.cn/disclosure/announcement/listing/stock/c/c_20220525_85597193.shtml
  - SSE investor education:
    http://edu.sse.com.cn/attention/a/20220414/c540ac01f593e02d2f2c46f33223951c.pdf
- ChiNext / STAR delisting arrangement: 20% after the first day (see 3.2 and
  the SZSE notice above).

### 3.6 Pre-2023 main-board IPO first day — order range vs price limit

- SZ (frozen): SZSE official 2014 rule — 深证会〔2014〕54号 (Sol-verified
  authority): full-day valid order range 64%..144% of issue price, rounded
  half-up to tick; PRICE_LIMIT = NONE.
  https://www.szse.cn/www/disclosure/notice/company/t20140613_508770.html
- SH: bounded SSE official-source research required. In V01.2 the SSE
  official source is still not captured, so the edge remains:
  SH_PRE_2023_IPO_ORDER_RANGE_OPEN
  This is an OPEN_NONBLOCKING_EDGE, not a hard blocker: PRICE_LIMIT = NONE on
  those days is a resolved row (limit_applicable=false, high_limit=NULL,
  low_limit=NULL, no_limit_reason=<enum>), so it produces zero
  unresolved_required_price_limit_rows and does not block
  PRICE_LIMIT_COMPLETE. It affects only future VALID_ORDER_PRICE_RANGE facts
  and must not be silently assumed to be the same 64%..144% as SZ without an
  SSE official source.

## 4. Minimum tick and rounding (R4D contract)

- SH/SZ A-share minimum tick = 0.01 CNY, per exchange Trading Rules
  (REBIND_AT_R4D — exact current article to be pinned at verification).
- Computation uses Decimal with ROUND_HALF_UP; no float epsilon business rule.
- minimum-one-tick rule: a computed limit that rounds back to the preclose is
  adjusted by +/- 1 tick.
- minimum-price-floor rule: limit prices respect the exchange absolute-price
  floor (>= tick magnitude; par-value floor rule pinned at R4D).

## 5. Versioning requirement

The rule table contains rule changes INSIDE the R3 window:

1. ChiNext 10% -> 20% effective 2020-08-24 (normal and risk-warning).
2. Main-board risk-warning 5% -> 10% effective 2026-07-06.

R4D must:

- pin the rule table with a deterministic version + hash;
- encode effective dates, not a single snapshot;
- carry the authority link per rule row;
- fail closed on any symbol/board/date combination without an unambiguous row.

## 6. Known edges and uncertainties (do NOT silently assume)

1. PRE_2023_MAIN_BOARD_IPO_ORDER_RANGE
   - SZ frozen at 64%..144% (深证会〔2014〕54号, Sol-verified).
   - SH open (SH_PRE_2023_IPO_ORDER_RANGE_OPEN) — OPEN_NONBLOCKING_EDGE;
     bounded SSE official research still required; does not block
     PRICE_LIMIT_COMPLETE (those days resolve to PRICE_LIMIT = NONE).
2. STAR_ST_HISTORICAL_SPLIT
   - Baostock `st_history` is binary isST; it does not distinguish ST from
     *ST. Historical subtype split is NOT_PROVIDED by pinned sources ->
     risk_warning_subtype=UNKNOWN under the is_risk_warning umbrella.
3. DELISTING_ARRANGEMENT_INTERVAL
   - R3 formal delisted map provides delist_date but not arrangement start;
     pinned feeds provide no delisting-arrangement flag. CONFIRMED_BLOCKER;
     affected price-limit rows UNKNOWN/PARTIAL; cannot back-derive.
4. RESOLVED_IN_V01_2 — 2026 main-board risk-warning official pages are bound:
   SZSE 深证上〔2026〕551号 and SSE 上证发〔2026〕41号 (both effective
   2026-07-06). No remaining open edge here.

## 7. Boundary

- OFFICIAL_EXTERNAL_RESEARCH_USED = YES
- EXTERNAL_SOURCE_DOMAINS:
  - sse.com.cn
  - szse.cn
  - csrc.gov.cn
- SOL_VERIFIED_OFFICIAL_ANCHORS_BOUND_N = 3
  - 深证会〔2014〕54号 (SZ pre-2023 main-board IPO valid order range)
  - 深证上〔2026〕551号 (SZSE 2026 Trading Rules, effective 2026-07-06)
  - 上证发〔2026〕41号 (SSE 2026 Trading Rules, effective 2026-07-06)
- CITATION_BINDING_ONLY = YES (no new rule research in V01.2)
- SECONDARY_AUTHORITY_SOURCES_N = 0
- CODE_FILES_CHANGED = 0
- MARKET_DATA_CHANGED = NO
- NETWORK_PROVIDER_DATA_FETCH = 0
- REAL_R4_EXECUTION = NO

This report is planning evidence only. It does not authorize implementation
or execution.
