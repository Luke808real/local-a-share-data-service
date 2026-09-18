# CNEquity 升级前工程现状

核验日期：2026-09-18。仓库：`/Users/luke808/ASL`。

提交说明：本文及 JSON 基线保留提交前的工作区快照。用户随后要求推送 GitHub，
本轮相关代码、补丁、测试和文档随本文件一并提交；下文“尚未提交”描述的是
核验时状态。当前源码提交以包含本文件的 Git commit 为准，无关遗留文件未纳入。
本次仅核对本地现状、生成交接文档；未安装新版本、修改依赖或执行数据更新。
下一目标版本尚未指定，本报告不声称已完成目标版本兼容性审核。

## 1. 当前工程结论

工程已有可用的日线更新、独立 Facts 发布、只读本地查询及 MCP 服务。
9 月 17 日日线和五项 Facts 已完成实际发布及回读，但新的 V02 发布链
尚未整合进旧的统一每日维护入口。当前属于“数据可用、局部能力经过验证，
统一调度和版本交接仍需收口”的状态，不能等同于所有路线图阶段完成。

CNEquity 是唯一数据库基础；ASL 在其上维护范围约束、质量认证、发布指针、
字段派生和查询服务。数据目录独立于源码目录。

## 2. Git 与可复现状态

- 分支：`codex/r3-incremental-hardening-daily-facts-phase1-v01`
- HEAD：`99b9326952ae96855c8c642007a8beaef030d182`
- 本地 upstream tracking ref 与 HEAD 相同，ahead/behind 为 0/0。
  本次没有 fetch，不能据此断言服务器端当前分支未变化。
- 工作区不干净：最新 9 月 17 日工作包括未提交修改和未跟踪新文件。
- 仅 checkout HEAD 不能复现当前生产运行状态；仅安装 pyproject 锁定版本
  也不能复现，因为已安装的 CNEquity 还带有三个 ASL 本地补丁。
- 本次生成的 JSON 基线记录相关文件 SHA256、工作区状态和运行版本。
  该记录是清单，不是源码或数据库备份。

本轮业务改动：

| 类型 | 路径 |
|---|---|
| 新 V02 认证/发布模块 | `src/ashare_data/daily_facts_v02_publish.py` |
| 新发布 CLI | `tools/publish_daily_facts_v02.py` |
| 新认证和文件兼容回归 | `tests/test_daily_facts_v02_publish.py` |
| 派生/查询调整 | `src/ashare_data/daily_facts_v02.py`, `local_query.py` |
| 日期保护/旧日比较修复 | `tools/run_daily_facts_v02_live_v01.py`, `run_daily_facts_v02_shadow_v01.py` |
| 本地补丁 0003 | `patches/cnequity/0003-snapshot-target-session-cutoff.patch` |
| 测试/文档 | `tests/test_snapshot_guard.py`, `tests/test_local_query.py`, `patches/cnequity/README.md`, `docs/PROJECT_STATE.md` |
| 执行证据 | `docs/plans/ASL_20260917_MINIMAL_UPDATE_AND_MCP.md`, `reports/implementation/ASL_20260917_LOCAL_FIELDS_COMPLETION.md` |

此外存在之前遗留的未跟踪规格、计划、历史 handoff、`tmp/` 和
`tests/test_cnequity_v080_daily_reuse.py`；它们不应被当成本轮新增而批量删除或提交。

## 3. 当前运行环境

| 项目 | 生产采集 `.venv` | 查询服务 `.venv-mcp` |
|---|---|---|
| Python | 3.12.13 | 3.12.13 |
| CNEquity | 0.8.0 + ASL 补丁 | 未安装 |
| DuckDB | 1.5.5 | 1.5.5 |
| PyArrow | 25.0.1 | 25.0.1 |
| Polars | 1.43.2 | 非本次核对项目 |
| pytest | 8.4.2 | requirements 指定 9.1.1 |
| MCP SDK | 非采集依赖基线 | 1.29.1 |

依赖入口：`pyproject.toml`、`uv.lock`、`requirements-mcp.txt`。
CNEquity 固定 git commit：`d453853da766b3ba3e44489c0fb6e0089243fa25`。
生产安装的 direct_url 与 pyproject/lock 相符，未使用浮动 main。
`.venv-cne080` 是此前并排审核环境，不是当前生产 writer。

三个补丁必须逐项与目标版本对比，不能直接丢弃或盲目重放：

| 补丁 | 必须保留的语义 |
|---|---|
| 0001 | EastMoney `f8` 写入 `valuation_metrics.turnover_rate`，单位为百分数；空值不变成 0 |
| 0002 | 请求 `f124`；校验真实交易日、一致性、收盘时间和换手率次日清零，阻止快照错标日期 |
| 0003 | 截止时间使用目标交易日完整 datetime；允许次日凌晨读取仍有效的上一日收盘快照，拒绝未来日期 |

补丁作用于已安装包；普通重装不会自动重新应用。
`patches/cnequity/contracts_registry_after_patch.json` 是 ASL 补丁后的契约；
上游自带 `contracts/v0.8.0.json` 保留上游原貌，二者不能混为一谈。
已安装文件哈希在 JSON 基线及 patches README 中记录。

## 4. 模块与依赖关系

| 模块 | 当前职责及状态 |
|---|---|
| `tools/run_r3_frozen_shsz_incremental_v01.py` | 冻结 SH/SZ 范围的逐日增量、TDX 获取、缺口分类、认证、最后切换指针；当前日线生产路径 |
| CNEquity `JobEngine` | `trading_status`, `valuation_metrics`, `corporate_actions` 原生批量更新与 compact |
| `src/ashare_data/daily_facts_v02.py` | 由已发布日线、原生数据及官方公告派生五字段 |
| `src/ashare_data/reference_price_evidence.py` | 精确日期的公告、哈希、有效现金分红与前收盘价依据验证 |
| `src/ashare_data/daily_facts_v02_publish.py` | 离线认证、完整字段序列化、DuckDB 回读、不可变文件和指针最后发布；新代码未提交 |
| `tools/run_daily_facts_v02_live_v01.py` | 预检和现场演练；仍不是完整自动发布入口 |
| `tools/run_asl_daily_maintenance.py` | 旧 V1 统一维护；仍连接逐股 BaoStock 获取和 V1 发布，不可当作新的 V02 生产入口 |
| `src/ashare_data/cnequity_bridge.py` | 旧 Facts 桥接，依赖 CNEquity BaoStock 私有 `_session` API |
| `src/ashare_data/local_query.py` | DuckDB/Parquet 本地只读查询，读取发布清单；不访问行情网络 |
| `src/ashare_data/mcp_server.py` | 只读 `status/instrument/bars/latest/facts`；独立服务环境 |

ASL 对 CNEquity 不仅有 CLI 依赖，还有内部 Python API 依赖：
`adapters.tdx_protocol.client`、`config.loader`、`storage.parquet.StagingWriter`、
`storage.atomic.write_parquet_atomic`、`orchestrator.manifest.Manifest`、
`orchestrator.engine.JobEngine`、`adapters.eastmoney.clist/em_auth`、
`domain.snapshot_guard` 以及 `adapters.baostock._session`。
新版即使 Parquet schema 不变，函数路径、参数、异常或 manifest 行为变化仍可能破坏 ASL。

## 5. 数据与发布基线（本次重新核对）

数据根：`/Users/luke808/AI/local-a-share-data-service-data`。

| 项目 | 状态 |
|---|---|
| 日线最新物理/发布日期 | 2026-09-17 / 2026-09-17 |
| 日线文件数 / 待发布数 | 2,603 / 0 |
| 正式身份范围 / 当日合格股票数 | 5,456 / 5,208 |
| Facts 最新新增日期 | 2026-09-17；V02 五字段 5,208 行 |
| 特例 | 24 条官方除权除息依据，12 只停牌，201 只 ST（包含 4 只停牌 ST） |
| Facts 历史缺口 | 2026-09-10 至 2026-09-16 未补；旧 V1 发布文件保留 |
| 全局标志 | FACTS_READY=false；PRECLOSE_COMPLETE=false；R7_FIRST_PUBLISH_PASS=false |
| 历史日线覆盖 | PARTIAL，未声称全历史认证 |

R3 manifest：
`e5364e8237e0776b139477f2284b93587bee8b0b5232b0122c583a2b28600a2b`

Facts manifest：
`6126f20103eae277f5e23f95f2e314a26f8b6c90b37cfb3e292e01f3dc36710b`

独立权威指针：
- `meta/asl/r3/published-daily-authority.json`
- `meta/asl/daily_facts/published-daily-facts-authority.json`

新 Facts 使用原有 V01 指针/清单封装，记录本身为 `ASL_DAILY_FACTS_V02`。
查询依赖清单，不应改为 glob 读取所有物理文件。staging 和被替代文件仍保留，
不代表当前发布版本。升级回滚必须同时考虑源码、运行环境、元数据/修订和发布指针；
仅降级 pip 包不能回滚一次已执行的 compact 或数据迁移。

## 6. 已知问题与升级重点

1. **旧维护入口误判 V02。** `facts_state()` 写死
   `part-full-eligible-v01.parquet`。本次只读实测对 2026-09-17 返回
   `published=true, quality_pass=true, day_present=false`。
   需要改为依据清单按日期识别，并明确接入 V02，才能用于统一日更。
2. **补丁未纳入自动安装。** 升级前逐项确认新版是否已实现相同语义；
   未覆盖的保留最小补丁并做回归。0001/0002 有此前干净版本重放记录，
   0003 已核验生产哈希及回归，但本次未重新完成三补丁全链干净环境重放。
3. **新发布模块尚属本地作者验证。** 99 项针对性测试和生产回读已通过，
   不是完整测试套件或独立审计；最新工作区尚未提交。
4. **Parquet/查询兼容需要实际读。** 最近修复过稀疏公告字段被 schema 推断遗漏、
   时区列引入未声明 pytz 依赖、全空 Null 列触发 DuckDB PlainSkip。
   必须测真实文件和带 symbol/date 谓词的读取，不能只比较 schema JSON。
5. **快照不能历史回放。** 新版必须保留 snapshot/trade-date 约束，不能用当前
   valuation/status 页面给过去日期补标签，也不能把 null ST 归一化为 false。
6. **默认任务范围比本次维护宽。** config 的 universe 为 all_a，原生 daily
   waves 含额外数据和派生步骤；不能把通用 daily/init 当作已获授权的最小更新。
7. **数据库路径仍有硬编码。** 新 publisher 校验生产 ROOT；若要隔离湖验证，
   先验证可注入路径的组件或做明确的可测试性适配，不能假定 CLI --data-root
   已支持任意目录，也不能删除 ROOT 保护以绕过限制。

## 7. 服务与验证证据

本次 launchctl 检查 `io.asl.market-data-mcp` 有活动进程。
服务监听入口：`http://127.0.0.1:8766/mcp`。
`io.asl.secure-mcp-tunnel` label 当时没有 launchd PID；这不等于已验证
独立 tunnel-client runtime 的健康，本次没有重新测远端或 ChatGPT Web。

上一执行回合：99 项 targeted tests 通过；普通、ST、停牌、除权除息、本地
MCP 和旧 V1 读取通过；非发布日期正确拒绝。本次只读状态核验再次通过，
没有重新运行测试。证据在：
`staging/asl_20260917_minimal_update_v01/{local_readback.json,local_mcp_readback.json,facts_publication_r4/}`。

基准回归命令（本地，非 provider 抓取）：

```bash
PYTHONPATH=src:tools .venv/bin/python -m pytest \
  tests/test_daily_facts_v02_publish.py tests/test_daily_facts_v02.py \
  tests/test_daily_facts_publication_types.py tests/test_snapshot_guard.py \
  tests/test_local_query.py -q
```

升级额外检查入口：`test_valuation_turnover_rate_v01.py`、
`test_r3_frozen_shsz_incremental_v01.py`、`test_cnequity_v080_daily_reuse.py`、
`test_daily_maintenance_v01.py`、`test_mcp_server.py`。
MCP 测试使用相应查询依赖环境；存在需要本地湖的测试，测试通过不等同于隔离湖验证。

## 8. 建议的下一道门

先确定目标 release 和精确 commit，在隔离环境完成“当前 0.8.0 + 三补丁”
对目标版本的契约/API/行为差异报告，不改生产 .venv，不写生产湖。
开始前保存本轮明确相关的未提交源文件和补丁，形成可复现源码基线；
数据库快照范围包括元数据、revision 与 authority，不只是 Parquet 文件。

差异报告至少回答：三个补丁是否仍需要；七个核心 dataset 的字段/单位/键/
日期语义是否兼容；内部 API 和缺口恢复是否兼容；旧文件、V1/V02 混合查询是否可读；
writer lock、compact、发布原子性是否保留；有无强制迁移及其回滚方式。
在这些结论明确之后，再制定有限范围更新与生产切换方案。
