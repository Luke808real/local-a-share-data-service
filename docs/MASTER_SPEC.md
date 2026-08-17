# LOCAL A-SHARE MARKET DATA SERVICE

**SPEC_VERSION:** `V1.0 FROZEN`  
**STATUS:** `APPROVED / FROZEN`  
**DESIGN_DATE:** `2026-08-17`

---

## 1. PURPOSE

本项目建设一个运行于用户 Mac 本地的独立 A 股历史市场数据服务。

核心目标：

> 建立快速、可靠、可追溯、可历史回看、可供 Codex / AI 查询的 A 股本地事实数据库，使用户在分析涨停回调候选股时，不再需要频繁从同花顺截图并人工搬运数据。

典型目标体验：

```text
用户：
东方锆业这次涨停后的回调怎么看？

↓

Local A-Share Market Data Service

resolve_symbol("东方锆业")
→ 002167.SZ

↓

Stock Context
→ 历史日线
→ 近期 5m
→ 换手率
→ 复权信息
→ ST / 停牌
→ 历史涨停事实
→ 申万行业
→ 同期市场环境
→ 数据质量

↓

Thinker / GPT Strategy Layer

→ T0
→ PULLBACK
→ B1
→ B2_READY
→ B2_CONFIRMED
→ 风险与操作分析
```

本项目只负责：

**MARKET FACTS + QUERY ACCESS**

不负责：

**STRATEGY INTERPRETATION**

---

## 2. CORE ARCHITECTURE DECISION

### 2.1 Database Foundation

`CNEquity` 是本项目 authoritative database foundation。

设计原则：

```text
CNEquity
↓
Authoritative Local Market Data
↓
Minimal Stable Market Facts
↓
Quality / Publish
↓
Thin Python Query Core
↓
CLI / Codex / MCP
```

本项目：

- 不重新实现 CNEquity；
- 不建立第二套 canonical market lake；
- 不大规模 fork CNEquity；
- 不复制 CNEquity 已经可靠提供的数据 contract；
- 只在必要位置增加薄扩展。

---

## 3. SYSTEM BOUNDARY

### 3.1 Data Layer Responsibilities

Data Service 可以负责：

- 股票身份；
- 历史 OHLCV；
- 成交额；
- 复权因子；
- 历史交易状态；
- ST / *ST；
- 停牌；
- 换手率；
- 涨跌停价格；
- 历史涨停事实；
- 5 分钟行情；
- 申万行业；
- 主要市场指数；
- 数据 coverage；
- provenance；
- AS_OF-safe 查询；
- 通用统计摘要。

### 3.2 Strategy Layer Responsibilities

以下内容禁止进入 Data Service：

```text
T0 quality
PULLBACK judgment
B1
B2_READY
B2_CONFIRMED
SECOND_LAUNCH

洗盘
主力吸筹
承接良好
买点
卖点
策略评分
成功概率
TradePlan
```

这些全部属于 Thinker / Strategy Skill / GPT Strategy Layer。

---

## 4. V1 NON-GOALS

V1 明确不建设：

- 实时行情；
- 盘中 5m 增量；
- Level-2；
- 逐笔；
- 订单簿；
- B1/B2 状态机；
- Watchlist；
- 回测平台；
- Forward；
- TradePlan；
- 券商交易；
- 自动下单；
- ML；
- Vector DB；
- 新闻系统；
- 公告分析系统；
- 龙虎榜分析系统；
- 主力资金模型；
- 概念热点系统；
- 完整板块情绪系统；
- 完整基本面平台；
- 云数据库；
- Supabase / Neon；
- 复杂 Web UI。

如果未来需要上述能力：

> 独立 Brainstorm → Spec → Plan。

禁止作为 V1 实现中的顺手扩展。

---

## 5. MARKET UNIVERSE

V1 authoritative universe：

```text
SSE
SZSE
BSE

+

当前上市股票
历史退市股票
```

数据库不得因为当前涨停回调策略不交易北交所、ST、退市股而删除这些历史事实。策略层自行过滤。

此设计用于避免 survivorship bias。

---

## 6. DATA ROOT

新项目必须建立独立、干净的 authoritative CNEquity data root。

Data Root：

- 必须与 Git repository 分离；
- 不允许存入 Git；
- 具体 Mac 路径由部署阶段配置；
- 最终路径必须记录在 `PROJECT_STATE.md`。

Legacy roots：

```text
/Users/luke808/AI/asl-shared
/Users/luke808/AI/asl-r8-5m-lake
/Users/luke808/AI/V flash/data
```

全部定义为：

`LEGACY_READ_ONLY_SOURCE`

正式 migration 决策前禁止：

- delete；
- move；
- rename；
- compact；
- repair；
- modify。

---

## 7. REQUIRED DATASETS

### 7.1 Instruments

至少：

```text
symbol
code
name
exchange
board

list_date
delist_date
security_type
```

必须支持历史退市证券。

标准 symbol 示例：

```text
002167.SZ
600519.SH
```

### 7.2 Daily Bars

权威价格语义：

> RAW / UNADJUSTED historical market price

至少：

```text
symbol
trade_date

open
high
low
close

volume
amount

source
data_version
fetched_at
```

#### Volume Contract

全库必须统一为明确单位。禁止部分数据为“手”、部分数据为“股”。

#### Amount Contract

全库单位必须统一，不得混用元/千元/万元。

---

## 8. ADJUSTMENT

权威数据永久保存：

```text
RAW price
+
adj_factor
```

Query Layer 支持：

```text
raw
qfq
hfq
```

默认：`raw`

用途分离：

```text
真实涨停 / 前收 / 实际成交价
→ RAW

长期走势 / 均线 / 跨除权价格结构
→ QFQ
```

不得只保存 qfq 而丢失真实成交价格。

---

## 9. CORPORATE ACTIONS

CNEquity 能可靠提供时保留除权、除息、送股、转增、配股等。

V1 默认 Stock Context 不返回完整 corporate actions。

主要用途：

- adj factor 验证；
- 异常价格解释；
- 数据质量审计；
- drill-down。

---

## 10. TRADING STATUS

必须是历史时序数据。

统一 Query Contract：

```text
NORMAL
SUSPENDED
ST
STAR_ST
DELISTING
UNKNOWN
```

不得：

- 用当前状态解释历史；
- 用 `volume == 0` 简单推断停牌。

历史 AS_OF 查询必须返回当时真实状态。

---

## 11. 5-MINUTE DATA

V1 保存：

> 全 A 股当前能够可靠获得的最大合理历史 5m 范围。

首次 Bootstrap：尽可能完整获取现有可获得历史。

之后：

```text
APPEND ONLY
NO AUTOMATIC DELETION
```

因此本地 5m 会随着时间自然积累成为长期历史资产。

至少：

```text
symbol
datetime

open
high
low
close

volume
amount

source
data_version
fetched_at
```

必须冻结并验证：

- timestamp semantic；
- left/right bar labeling；
- volume unit；
- amount unit；
- trading session；
- `(symbol, datetime)` PK。

5m 不永久保存 MA、MACD、B1、B2、分时策略标签。

---

## 12. TURNOVER

`turnover_rate` 是 V1 REQUIRED FACT。

采用混合策略。

### Primary

优先使用可信 Provider 直接提供的历史换手率。

必须同时记录：

```text
turnover_rate
turnover_source
turnover_semantic
coverage
```

### Secondary Validation

如果可以可靠获得 `float_shares`，允许进一步计算 `derived_turnover` 用于 reconciliation。

V1 不要求历史 float shares 成为唯一实现路径。

禁止：

```text
missing turnover → 0
missing turnover → silently accepted
```

缺失必须显式暴露：`TURNOVER_PARTIAL`。

---

## 13. STABLE MARKET FACTS

本项目允许维护一层极薄、策略无关的 Stable Market Facts。

允许物化：

```text
symbol
trade_date

preclose

pct_change
amplitude

turnover_rate
turnover_source

high_limit
low_limit

is_limit_up
is_limit_down

facts_version
rule_version
computed_at
```

允许进入此层的字段必须同时满足：

1. 通用；
2. 非策略；
3. 定义稳定；
4. 高频使用；
5. 可以明确追溯到 authoritative facts。

明确禁止永久物化：

```text
MA5
MA10
MA20

volume_ratio

rolling_high
rolling_low

distance_to_high

pullback_days

T0
B1
B2

setup_score
```

这些属于 Query-time Analytics 或 Strategy Layer。

---

## 14. PRICE LIMIT RULE ENGINE

历史涨跌停价格不得使用机械 `preclose × 1.10`。

必须考虑：

- 主板；
- 创业板；
- 科创板；
- 北交所；
- ST；
- *ST；
- 历史制度；
- 特殊上市交易规则；
- tick precision。

Price Limit Rule Engine 输入：

```text
symbol
trade_date
board
trading_status
preclose
```

输出：

```text
high_limit
low_limit
rule_id
```

示例 rule IDs：

```text
MAIN_10_V1
ST_5_V1
CHINEXT_20_V1
STAR_20_V1
BSE_30_V1
```

涨跌停判断必须基于 `RAW PRICE`。

---

## 15. MARKET CONTEXT

V1 保存基础市场背景。

核心指数至少包括：

```text
上证指数
深证成指
创业板指
沪深300
中证500
中证1000
```

主要使用日线。

指数 5m 可以保存，但不是 MVP blocker。

---

## 16. INDUSTRY

默认主行业口径：`PRIMARY_INDUSTRY = 申万`

至少：

```text
SW Level 1
SW Level 2
```

Level 3 数据稳定则保留，但不是核心 V1 blocker。

其他行业分类如果 CNEquity 已经存在，可以保留为 metadata，但默认 Stock Context 不混用多种行业分类。

历史查询应尽可能使用历史有效行业 membership，避免 future classification leakage。

---

## 17. DATA SOURCE POLICY

采用：`PRIMARY + FALLBACK`

流程：

```text
Primary
↓ success
accepted

Primary
↓ availability failure

Fallback
↓ success
accepted with explicit provenance
```

Fallback 必须：

- 满足相同 schema；
- 满足相同单位；
- 满足相同字段语义；
- 保留 source provenance。

Provider 返回实质冲突时：

```text
DATA_CONFLICT
→ QUALITY FLAG / FAIL CLOSED
```

禁止“哪个有值就随便选哪个”。同时禁止重新构建 VFlash 式“所有 Provider 每天全量对账”体系。

---

## 18. INITIAL BOOTSTRAP STRATEGY

默认采用：

> LEGACY REUSE FIRST → GAP BACKFILL

流程：

```text
Legacy Inventory
↓
Compatibility
↓
Verified Migration
↓
Gap Analysis
↓
CNEquity Native Backfill
```

不得先重新下载全部历史，再研究旧数据是否可用。

---

## 19. LEGACY MIGRATION DECISIONS

每个 Dataset 只能选择：

```text
DIRECT_REUSE
MIGRATE_AFTER_NORMALIZATION
CROSSCHECK_ONLY
REJECT
```

### DIRECT_REUSE

要求 schema / units / PK / timestamp compatible，provenance acceptable，coverage explainable。

### MIGRATE_AFTER_NORMALIZATION

允许明确、版本化转换，例如 volume lot → shares。必须记录 normalization_rule / migration_version / source。

### CROSSCHECK_ONLY

旧数据仅作 Golden / reconciliation evidence。

### REJECT

包括策略状态、cache、screen runs、generations、TradePlan、unknown provenance、ambiguous unit、contaminated data。

---

## 20. LEGACY DATA PRIORITY

迁移优先级：

```text
Tier 0
trading_calendar
instruments

Tier 1
daily_bars

Tier 2
adj_factors
corporate_actions
trading_status

Tier 3
minute_bars_5m

Tier 4
turnover / float_shares

Tier 5
industry
index
```

旧 5m 历史属于高价值资产，必须优先保护，避免因数据源历史窗口滚动而永久丢失。

---

## 21. MIGRATION SAFETY

所有 Legacy sources：`READ ONLY`

Migration 必须生成可追溯 Receipt：

```text
dataset
source_root
source_version

source_rows
accepted_rows
rejected_rows

min_date
max_date

normalization_rules

validation_status
```

Migration 完成后 Legacy Layer 不进入长期 EOD 主路径。

---

## 22. UPDATE MODEL

V1 更新频率：`EOD ONLY`

不做盘中 5m、实时 quote。

更新系统采用唯一主 Pipeline：

```text
EOD_UPDATE(as_of=D)
```

支持 Manual + Future Scheduler。

scheduler 只负责触发同一个 Pipeline，不得存在两套业务更新逻辑。

---

## 23. EOD STATE MODEL

每个交易日逻辑状态：

```text
BUILDING
VALIDATED
PUBLISHED

FAILED
```

Query Core 只认 `PUBLISHED`。

---

## 24. LATEST GOOD STATE

系统必须维护：

```text
latest_good_as_of
published_batch_id
published_at
quality_report_hash
```

默认 Query：`AS_OF = latest_good_as_of`

当天更新失败时 `latest_good_as_of` 保持不变。

Query 不允许看到半完成 Candidate。

---

## 25. CANDIDATE MODEL

Candidate 是逻辑 EOD batch，不是完整复制几十 GB 数据库。

如果 CNEquity 本身提供可靠原子/批次能力，应优先复用。

即使 candidate rows 已物理写入本地湖，Query Core 也必须通过 `latest_good_as_of` / Published contract 排除未发布日期。

---

## 26. EOD PIPELINE

固定为：

```text
1. Trading-Day Gate
2. Preflight
3. CNEquity Update
4. Stable Facts Build
5. Quality Gate
6. Publish
7. Receipt
```

---

## 27. TRADING DAY / PREDECESSOR

非交易日：`NOOP_NON_TRADING_DAY`

更新必须保持交易日连续。

如果 `latest_good_as_of = D-2` 而 D-1 缺失，不得直接 Publish D。

自动模式可以 D-1 PASS 后再 D；显式跳日请求默认 fail closed。

---

## 28. IDEMPOTENCY

已成功 Publish 的 D 再次执行普通 `update D`，默认返回 `ALREADY_PUBLISHED`。

不得无理由重新下载或改写。

Repair / Revalidate 必须使用独立显式语义。

---

## 29. UPDATE LOCK

同一时刻只允许一个 EOD writer。

如果 scheduler 与人工执行冲突，第二个 writer 不进入正式更新，返回 `EOD_UPDATE_RUNNING` 或等价 fail/noop 状态。

与此同时 Query 可以继续读取上一份 Published 状态。

---

## 30. QUALITY MODEL

Quality Gate 分四层：

```text
L0 STRUCTURAL
L1 DATASET
L2 CROSS-DATASET
L3 PUBLICATION
```

---

## 31. L0 STRUCTURAL

检查：

- schema；
- types；
- required columns；
- PK；
- readable storage；
- metadata；
- Parquet integrity。

失败：`BLOCK_PUBLISH`

---

## 32. L1 DATASET QUALITY

### Daily

至少：

```text
duplicate PK = 0
conflicting duplicate = 0

low <= open <= high
low <= close <= high

volume >= 0
amount >= 0
```

### 5m

至少：

```text
duplicate(symbol, datetime) = 0
valid OHLC
valid session
valid timestamp semantics
valid units
```

### Turnover

至少检查 missing coverage、negative values、source、semantic、obvious anomalies。

Hard invalid 与 soft anomaly 必须区分。

---

## 33. L2 CROSS-DATASET

至少包括：

### Daily ↔ 5m

合理范围内校验 open / high / low / close / volume / amount。

### Status ↔ Bars

SUSPENDED 不应机械要求正常全天 5m。

### Daily ↔ Turnover

正常交易股票大面积缺失 turnover：`COVERAGE_FAILURE`

### Daily ↔ Price Limits

`is_limit_up=true` 必须与历史涨停价一致。

---

## 34. COVERAGE SEMANTICS

Quality Engine 必须区分：

```text
EXPECTED
OBSERVED
EXPLAINED_MISSING
UNEXPLAINED_MISSING
```

Fail Closed 不意味着任何一行缺失都让全市场永久无法发布。

合法停牌导致无 5m：`EXPLAINED_MISSING`

正常交易股票异常无 daily：`UNEXPLAINED_MISSING`

---

## 35. EOD RECEIPT

每个成功/失败 EOD batch 都保存轻量 receipt。

至少：

```text
batch_id
as_of

started_at
finished_at

previous_good_as_of

runtime_version
config_hash

dataset results

primary_rows
fallback_rows

quality results

publish_status

latest_good_as_of
```

失败 receipt 不得更新 latest_good。

---

## 36. QUERY CORE

Python Query Core 是唯一标准业务查询实现。

正式接口控制在：

```text
resolve_symbol()
get_daily()
get_5m()
get_market_context()
get_industry_context()
get_stock_context()
get_data_status()
```

CLI / MCP 不重新实现业务查询逻辑。

---

## 37. SYMBOL RESOLUTION

支持：

```text
002167
002167.SZ
东方锆业
```

最终返回唯一标准 symbol。

名称歧义：`AMBIGUOUS_SYMBOL`

不得猜测。

必须支持历史退市证券。

---

## 38. HISTORICAL AS_OF

默认：`requested_as_of = latest_good_as_of`

允许：`--as-of YYYY-MM-DD`

所有相关数据与动态计算必须严格 `<= effective_as_of`，包括 daily、5m、turnover、status、industry、index、rolling statistics。

禁止未来数据泄漏。

如果用户请求日期晚于 latest good，必须显式返回：

```text
REQUESTED_AS_OF
EFFECTIVE_AS_OF
AS_OF_STATUS = CAPPED_TO_LATEST_GOOD
```

不得静默降级。

---

## 39. GET_DAILY CONTRACT

主要输入：

```text
symbol
as_of
lookback
adjust
```

`lookback` = trading days。

支持 raw / qfq / hfq，默认 raw。

---

## 40. GET_5M CONTRACT

5m 主要用于 drill-down。

支持：

```text
symbol
date
```

或 bounded `start_date` / `end_date`。

单股查询不得演变成全市场分钟扫描接口。

---

## 41. STOCK CONTEXT DEFAULT WINDOW

默认：

```text
DAILY_ANALYSIS_WINDOW = 250 trading days
RECENT_DAILY_DETAIL = 30 trading days
INTRADAY_ANALYSIS_WINDOW = 20 trading days
```

不得默认把 250 daily rows + 20 天全部 5m 倾倒给 Agent。

---

## 42. STOCK CONTEXT STRUCTURE

默认返回：

```text
1. identity
2. as_of
3. data_quality
4. current_market_facts
5. daily_structure
6. recent_daily_rows
7. limit_up_history
8. market_industry_context
9. intraday_context
10. drilldown_hints
```

---

## 43. QUERY-TIME ANALYTICS

允许动态计算：

```text
MA5
MA10
MA20
MA60

rolling high / low
price position
volume averages
volume ratios
turnover averages / percentiles
recent limit-up dates
market index returns
industry returns
```

Technical structure 默认 `QFQ`；Market facts 使用 `RAW`。

输出必须明确 `price_basis`。

---

## 44. INTRADAY SUMMARY

首次 Stock Context 不返回全部 5m bars。

可以按交易日生成事实摘要：

```text
date
open
high
low
close
first_30m_return
morning_return
afternoon_return
intraday_range
volume
morning_volume_share
first_30m_volume_share
high_time
low_time
close_position_in_range
```

禁止出现抢筹、洗盘、承接、B2确认等策略解释。

---

## 45. DRILL-DOWN

Agent 先读取 Stock Context。

发现关键日期后：

```text
get_5m(symbol, date)
```

读取完整 5m 原始行情。

允许事实型 hints：

```text
LIMIT_UP_DAY
MAX_VOLUME_20D
MAX_DRAWDOWN_FROM_20D_HIGH
```

禁止 `POSSIBLE_B1` / `POSSIBLE_B2`。

---

## 46. QUERY NETWORK RULE

正常 Query 必须完全本地。

包括 resolve_symbol / get_daily / get_5m / get_stock_context。

禁止 Query 时自动访问东方财富、TDX remote、AKShare 或其他公网数据源。

本地数据陈旧：`DATA_STALE`

联网只属于 Update / Backfill。

---

## 47. QUERY PERFORMANCE TARGET

本地 warm query 工程目标：

```text
resolve_symbol ≈ near-instant
stock-context < ~1 second
single-day 5m < ~500ms
```

这不是实时 SLA。

如果单股查询长期需要 10–30 秒，必须重新审查查询架构。

---

## 48. DATA QUALITY OUTPUT

Stock Context 必须附：

```text
daily status / coverage
5m status / coverage
turnover status / coverage
adjustment status
trading_status status
industry status
latest_good_as_of
staleness_trading_days
```

数据质量必须成为分析上下文的一部分。

---

## 49. ACCESS ADAPTERS

架构：

```text
Python Query Core
      │
      ├── Codex
      ├── CLI
      └── MCP
```

CLI 示例：

```text
./ops status
./ops update
./ops catch-up
./ops validate DATE
./ops stock-context 002167
```

第一版不设计大量管理命令。

---

## 50. MCP / CHATGPT

MCP 属于后期 Access Layer。

不得为了 MCP 改变数据 schema、publish semantics、AS_OF contract、quality semantics。

ChatGPT Web integration 不属于 Local Data MVP blocker。

---

## 51. REPOSITORY STRUCTURE

建议：

```text
repo/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .gitignore
├── config/
├── src/
│   └── ashare_data/
│       ├── runtime/
│       ├── migration/
│       ├── facts/
│       ├── quality/
│       ├── publish/
│       ├── query/
│       └── cli/
├── tests/
├── docs/
│   ├── MASTER_SPEC.md
│   ├── ROADMAP.md
│   ├── PROJECT_STATE.md
│   ├── DECISIONS.md
│   ├── contracts/
│   └── superpowers/
│       ├── specs/
│       └── plans/
├── reports/
│   ├── migration/
│   ├── quality/
│   └── audits/
└── ops
```

实际现有仓库结构如已存在合理约定，Codex 应优先遵循现有结构，禁止仅为匹配本示意图而无意义重构。

---

## 52. PROJECT DOCUMENT AUTHORITY

优先级：

```text
1. User current explicit instruction
2. MASTER_SPEC
3. PROJECT_STATE
4. ROADMAP
5. Phase Plan
6. Historical reports
7. Agent assumptions
```

---

## 53. MASTER SPEC

回答：WHAT / WHY / BOUNDARIES / ARCHITECTURE / CONTRACT / QUALITY / MVP。

Codex 不得在执行 Task 中自行改变 Spec。

发现冲突：`DESIGN_DECISION_REQUIRED`

---

## 54. ROADMAP

只保存 Phase / Goal / Entry Gate / Exit Gate，不作为详细 implementation plan。

---

## 55. PROJECT STATE

保持短小。

至少：

```text
AS_OF
SPEC_VERSION
CURRENT_PHASE
UPSTREAM_CNEQUITY
BRANCH
HEAD
WORKTREE
DATA_ROOT
LATEST_GOOD_AS_OF
DATASET_STATUS
BLOCKERS
LAST_AUDIT
NEXT_ACTION
```

只有经过正式 audit 后才能更新 authoritative PASS 状态。

---

## 56. CODEX EXECUTION MODES

任务仅分：

```text
RESEARCH
IMPLEMENTATION
VALIDATION
```

Codex不得自行跨 Phase。

---

## 57. CODEX TASK CONTRACT

每个实施任务包含：

```text
TASK_ID
SPEC_VERSION
BASE_HEAD
PHASE
MODE
GOAL
READ_FIRST
INPUT
SCOPE
DO_NOT
VERIFY
OUTPUT
COMMIT / PUSH
RETURN_CONTRACT
```

Task 应尽量 bounded。默认不允许无目的全仓扫描。

---

## 58. FULL-MARKET POLICY

默认：FAST TEST → BOUNDED VALIDATION。

只有正式质量门需要时，Task 明确写 `FULL_MARKET_AUTHORIZED = YES` 才允许长时间全市场运行。

---

## 59. REVIEW MODEL

Codex 报告中的 `STATUS = PASS` 仅代表 `AUTHOR_STATUS`，不是 `AUDIT_PASS`。

正式流程：

```text
Codex
→ implementation
→ tests
→ report
→ commit
→ push

GPT-5.6 Sol
→ exact SHA review
→ diff
→ implementation
→ tests
→ Spec comparison

→ AUDIT_PASS / REJECT
```

只有 `AUDIT_PASS` 才能推进 authoritative Project State。

---

## 60. AGENT AUTHORITY

### GPT-5.6 Sol

负责 architecture、decisions、contracts、quality gates、plan review、task design、independent audit、Go/No-Go。

### Codex

负责 local inspection、implementation、tests、data execution、migration、verification、commit、push、author report。

### Lower-cost Subagents

允许负责 bounded Python execution、schema scanning、row counts、duplicate detection、coverage calculation、mechanical tests、bounded review。

不得拥有 architecture decision、contract change、quality gate bypass、strategy decision。

---

## 61. SUPERPOWERS WORKFLOW

正式采用：

```text
Brainstorming
↓
Design / Master Spec
↓
Writing Plans
↓
Implementation
↓
TDD
↓
Verification
↓
Review
```

每个大型 Phase 独立生成 implementation plan。

不生成一个覆盖 R0–R10 的超巨型 Plan。

---

## 62. PHASE ROADMAP

### R0 — SPEC FREEZE

产物：

```text
MASTER_SPEC V1.0
ROADMAP
AGENTS
PROJECT_STATE
```

Exit：设计正式批准并冻结。

### R1 — LOCAL ASSET AUDIT

只读检查：

```text
CNEquity upstream
asl-shared
asl-r8-5m
VFlash
```

产物：

```text
Legacy Inventory
Compatibility Matrix
Coverage Map
Reuse Decisions
```

禁止网络历史全量下载。

### R2 — CNEQUITY BASELINE

建立 clean authoritative data root、fixed CNEquity runtime/version、config、storage contract。

### R3 — DAILY FOUNDATION

```text
legacy daily validation
↓
migration
↓
gap map
↓
CNEquity backfill
↓
daily quality
```

Exit：`DAILY_READY`

### R4 — MARKET FACTS

建立 adj、trading_status、turnover、preclose、price limits、limit-up facts。

Exit：`FACTS_READY`

### R5 — 5M HISTORY

```text
legacy 5m audit
↓
timestamp / unit contract
↓
migration
↓
gap backfill
↓
daily reconciliation
```

Exit：`5M_READY`

### R6 — MARKET / INDUSTRY

建立 market indices、SW industry、historical context。

Exit：`MARKET_CONTEXT_READY`

### R7 — QUALITY + FIRST PUBLISH

完成 L0/L1/L2/L3，第一次产生 `LATEST_GOOD_AS_OF = D`。

Exit：`FIRST_PUBLISH_PASS`

### R8 — QUERY CORE / STOCK CONTEXT

正式实现 Query Contract。

Exit：`LOCAL_DATA_MVP = PASS`

### R9 — EOD OPERATIONALIZATION

完成 manual EOD、catch-up、resume、receipts、locking、scheduler。

先手动稳定，再加 macOS scheduler。

### R10 — AI ACCESS

独立扩展 Local MCP、ChatGPT App、Thinker Skill、ChatGPT Web Integration。

不得反向改变数据库核心 contract。

---

## 63. LOCAL DATA MVP FORMULA

```text
LOCAL_DATA_MVP =
DAILY_READY
AND FACTS_READY
AND 5M_READY
AND MARKET_CONTEXT_READY
AND FIRST_PUBLISH_PASS
AND QUERY_CORE_PASS
```

Scheduler automation、MCP、ChatGPT Web Bridge 不属于 MVP 必需。

---

## 64. MVP HARD ACCEPTANCE GATES

V1 至少通过：

1. 任意正常沪深北 A 股可 resolve；
2. 历史退市证券可 resolve；
3. 多年 RAW daily 可查询；
4. volume / amount 单位可靠；
5. raw/qfq/hfq 可查询；
6. 历史 ST / suspension 可查询；
7. turnover 有明确 source / semantic / coverage；
8. 历史 price limits 可可靠派生；
9. 历史 limit-up facts 可可靠识别；
10. 全市场当前可获得 5m 已尽可能保存；
11. 5m timestamp/unit/session contract 已验证；
12. 主要指数可查询；
13. 申万 L1/L2 可查询；
14. historical AS_OF 无 future leakage；
15. 默认查询只读取 Published view；
16. Query 完全本地；
17. 缺数据显式暴露；
18. 单股查询性能满足本 Spec 工程目标；
19. Stock Context 支持 summary → drill-down；
20. Data Service 中不存在 B1/B2/策略交易语义。

---

## 65. FIRST REAL-WORLD ACCEPTANCE

最终必须用真实股票覆盖：

```text
沪市主板
深市主板
创业板
科创板
北交所
ST
历史退市股
```

分别验证：

```text
resolve
daily
qfq
turnover
status
5m
industry
AS_OF
stock-context
```

最后选择真实涨停回调候选股。

在不使用用户同花顺截图的情况下，让 Thinker 能基于 Local Stock Context 完成：

```text
T0质量
回调天数
量能
均线
位置
换手
近期5m
市场环境
行业环境
```

当该流程稳定成立时：`LOCAL_A_SHARE_DATA_MVP = PASS`

---

## 66. CORE DECISIONS

**D001** CNEquity 是 authoritative database foundation。  
**D002** Data Service 与 Strategy Layer 严格分离。  
**D003** 使用独立 clean authoritative data root。  
**D004** Legacy ASL / VFlash 只读审计后迁移。  
**D005** Legacy reuse 优先于重新下载。  
**D006** 全 A 股 = 沪深北 + 历史退市证券。  
**D007** RAW price authoritative。  
**D008** QFQ/HFQ 查询时动态生成。  
**D009** Turnover 使用 Provider direct + provenance，并预留 float-shares reconciliation。  
**D010** 全市场 5m 首次尽可能补齐，之后只增不删。  
**D011** V1 只做 EOD，不做盘中。  
**D012** Primary + Fallback，Fallback 必须显式 provenance。  
**D013** 只物化少数 Stable Market Facts。  
**D014** Candidate → Quality Gate → Publish。  
**D015** `latest_good_as_of` 是默认 Query authority。  
**D016** Python Query Core 是标准查询业务实现。  
**D017** Stock Context 采用摘要 + drill-down 两层模式。  
**D018** 默认窗口为 250 日 daily + 30 日日线明细 + 20 日 5m 摘要。  
**D019** 加入基础市场环境。  
**D020** 申万行业是 PRIMARY_INDUSTRY。  
**D021** 默认最新，同时支持 strict historical AS_OF。  
**D022** Query 严禁联网。  
**D023** MCP / ChatGPT Web 不属于 Local Data MVP blocker。  
**D024** Codex AUTHOR_PASS 不等于正式 AUDIT_PASS。  
**D025** Master Spec → Roadmap → Project State → Phase Plan → Codex Task 构成长期工程控制链。

---

## 67. HIGHEST PRINCIPLE

本项目的最高工程原则：

> **保存可靠、长期可复用的市场事实；快速向 Agent 提供高信息密度股票上下文；不要把数据库再次演化成策略系统。**

最终目标不是数据库本身。

最终目标是：

> **用户提出候选股票后，ChatGPT / Codex 可以直接读取本地可信历史事实完成分析，而用户不再承担行情截图与数据搬运工作。**
