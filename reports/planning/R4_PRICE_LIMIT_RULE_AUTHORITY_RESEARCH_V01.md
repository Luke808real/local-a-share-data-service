# R4 PRICE-LIMIT RULE AUTHORITY RESEARCH — V01

STATUS: AUTHOR_ONLY_PENDING_SOL_AUDIT
RESEARCH_DATE: 2026-08-19
SCOPE: SH/SZ price-limit basis for R4 stable_market_facts, window 2016-01-01..2026-08-17.
BJ recorded for completeness only; BJ_EXTENSION = DEFERRED.

This report records the official rule basis used by the proposed R4D rule
engine. It is a research / planning artifact. It does not implement code and
does not modify any market data.

## 1. Rule table (SH/SZ, window 2016-01-01 .. 2026-08-17)

| Segment | Period (trading effective) | Limit | Official basis |
|---|---|---|---|
| SH/SZ main board normal stocks | full window | 10% | Exchange trading rules (SSE/SZSE) |
| SH/SZ main board risk-warning (ST / *ST) | until 2026-07-05 | 5% | SSE/SZSE pre-2026 rules; SSE factbook / investor Q&A |
| SH/SZ main board risk-warning (ST / *ST) | from 2026-07-06 | 10% | SSE/SZSE Trading Rules (2026 revision); SSE technical notice; effective date 2026-07-06 |
| ChiNext 300xxx normal stocks | until 2020-08-21 | 10% | Pre-reform SZSE ChiNext rules |
| ChiNext 300xxx normal stocks | from 2020-08-24 | 20% | SZSE ChiNext Special Provisions (2020-08-24 effective) |
| ChiNext risk-warning (ST / *ST) | until 2020-08-21 | 5% | Pre-reform SZSE ChiNext risk-warning rule |
| ChiNext risk-warning (ST / *ST) | from 2020-08-24 | 20% | SZSE notice t20200710_579459 |
| STAR 688xxx | from 2019-07-22 | 20% | SSE STAR board trading rules / SSE investor education |
| STAR CDR 689xxx | from 2019-07-22 | 20% | SSE STAR board rules (CDR included in board scope) |
| STAR / ChiNext delisting-arrangement | after first day | 20% | SZSE notice t20200710_579459; SSE delisting announcements |
| Main board delisting-arrangement | after first day | 10% | SSE delisting announcements / rules 9.6.x |
| IPO first 5 trading days (STAR / ChiNext) | from board launch | NO LIMIT | SSE / SZSE rules |
| IPO first 5 trading days (main board) | from 2023-04-10 | NO LIMIT | CSRC full-registration reform; first batch 2023-04-10 |
| Main board IPO first day (pre-2023) | until 2023-04-07 | EDGE — 44% first-day cap to validate | OPEN_EDGE; no pinned authoritative source in this pass |
| BSE | full window | 30%; first day no limit | BSE official disclosure (recorded; BJ deferred) |

## 2. Effective dates and official sources

### 2.1 STAR board (科创板) — 20%

- SSE investor education (科创板投教下午茶): STAR board price limit is 20%;
  IPO stocks have no limit for the first 5 trading days.
  - http://edu.sse.com.cn/tib/ysptj/c/4768085.shtml

### 2.2 ChiNext (创业板) — 20% from 2020-08-24

- SZSE ChiNext Special Provisions, Rule 2.1: 20% price limit; IPO stocks have
  no limit for the first 5 trading days.
  - http://www.szse.cn/lawrules/rule/repeal/rules/P020231230545310237980.pdf
  (text of the Special Provisions; the URL path marks it as later superseded —
  keep as historical rule text, not current authority)
- SZSE notice on ChiNext risk-warning and delisting-arrangement trading:
  after the Special Provisions take effect, risk-warning stocks and
  delisting-arrangement stocks are 20%.
  - http://www.szse.cn/disclosure/notice/general/t20200710_579459.html
- SZSE document confirming the 2020-08-24 effective date for the 20% change
  (fund list effective 2020-08-24):
  - https://docs.static.szse.cn/www/aboutus/trends/conference/W020200821767425488573.pdf

### 2.3 Main board full registration — IPO first 5 days no limit from 2023-04-10

- CSRC Q&A on full registration reform: for main board IPOs, no price limit
  for the first 5 trading days; from the 6th trading day the limit remains 10%.
  - http://www.csrc.gov.cn/jiangsu/c105409/c7176801/content.shtml
  - http://www.csrc.gov.cn/csrc/c100028/c7047624/content.shtml
- CSRC Shanghai: first batch of main-board registration IPOs listed on
  2023-04-10, marking the full landing of the reform.
  - http://www.csrc.gov.cn/shanghai/c105566/c7402810/content.shtml

### 2.4 Main board risk-warning (ST / *ST) — 5% -> 10% from 2026-07-06

- SSE revision process: draft notice adjusting main-board risk-warning stock
  price limit from 5% to 10%, later incorporated into the Trading Rules
  (2026 revision).
  - https://www.sse.com.cn/aboutus/mediacenter/hotandd/c/c_20260410_10814807.shtml
- SSE technical notice requiring front-end price checks at the 10% limit for
  main-board risk-warning stocks.
  - https://www.sse.com.cn/services/tradingtech/notice/c/10783253/files/27d16cba23f847f6a9c9dd7320e5b20c.pdf
- Effective date 2026-07-06: exchange-mandated risk-disclosure update
  (broker notice) and market coverage.
  - https://www.newone.com.cn/newonefront/know-detail.html?useNewApi=1&detail=50858
  - https://m.chinatimes.net.cn/article/153949.html

### 2.5 Delisting arrangement (退市整理期)

- Main board: first trading day has no price limit; remaining days 10%.
  - SSE termination announcement example:
    http://www.sse.com.cn/disclosure/announcement/listing/stock/c/c_20220525_85597193.shtml
  - SSE investor-education PDF (退市整理股票 10%, first day no limit):
    http://edu.sse.com.cn/attention/a/20220414/c540ac01f593e02d2f2c46f33223951c.pdf
- ChiNext / STAR delisting arrangement: 20% after the first day (see 2.2 and
  the SZSE notice above).

### 2.6 BSE (北京证券交易所) — recorded for completeness, deferred

- First trading day no price limit; thereafter 30%; delisting-arrangement
  first day no limit, thereafter 30%.
  - http://www.bse.cn/disclosure/2026/2026-07-08/1783505295_289492.pdf
  - http://www.bse.cn/disclosure/2025/2025-12-26/c0744890832f4ba98ac837c26dc05087.pdf

## 3. Versioning requirement

The rule table contains a rule change INSIDE the R3 window
(main-board ST 5% -> 10% effective 2026-07-06). Therefore R4D must:

- pin the rule table with a deterministic version + hash;
- encode effective dates, not a single snapshot;
- carry the authority link per rule row;
- fail closed on any symbol/board/date combination without an unambiguous row.

## 4. Known edges and uncertainties (do NOT silently assume)

1. PRE_2023_MAIN_BOARD_IPO_FIRST_DAY
   - Pre-2023-04-10 main-board IPOs are commonly described as having a 44%
     first-day cap (20% call auction + 44% intraday). No pinned official
     authoritative citation was captured in this bounded pass; treat as
     OPEN_EDGE and validate separately before persisting facts for those days.
2. STAR_ST_HISTORICAL_SPLIT
   - Baostock `st_history` is binary `isST`; it does not distinguish ST from
     *ST (STAR_ST). Historical *ST split is NOT_PROVIDED by pinned sources.
3. DELISTING_ARRANGEMENT_INTERVAL
   - R3 formal delisted map provides `delist_date` but not arrangement start
     dates; pinned feeds do not provide a delisting-arrangement flag.
4. CHINEXT_PRE_REFORM_RISK_WARNING
   - Pre-2020-08-24 ChiNext risk-warning 5% row is common knowledge but was not
     re-verified from an official page in this bounded pass; flag for Sol
     review before persisting.
5. CDR_RULES
   - STAR CDR 689xxx follows 20%; SZSE technical documents mention ChiNext
     CDR 20%. No 689xxx-style SZSE CDR is expected in the current SH/SZ
     universe; engine should still carry the rule explicitly.

## 5. Boundary

- OFFICIAL_EXTERNAL_RESEARCH_USED = YES
- EXTERNAL_SOURCE_DOMAINS:
  - sse.com.cn
  - szse.cn
  - csrc.gov.cn
  - bse.cn
  (secondary market coverage used only to confirm the 2026-07-06 effective
  date: newone.com.cn, chinatimes.net.cn)
- CODE_FILES_CHANGED = 0
- MARKET_DATA_CHANGED = NO
- REAL_R4_EXECUTION = NO

This report is planning evidence only. It does not authorize implementation
or execution.
