# DECISIONS

Authority: `docs/MASTER_SPEC.md`
Rule: New decisions append; superseded decisions remain visible and identify the superseding decision.

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
