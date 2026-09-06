# Handoff — local CNEquity data and read-only MCP

Date: 2026-09-06

Repository: `/Users/luke808/ASL`

Branch: `codex/local-market-data-mcp-v01`
Implementation base HEAD: `25f4f5434b7b99a1461cf56343552c9f50100695`

## Purpose

The current work is a thin, read-only access layer over the existing CNEquity
data root. It is intended to let ChatGPT read already published local daily
market facts. It is not an ingestion, repair, R4, strategy, or database
migration task.

## Data state

Data root:

`/Users/luke808/AI/local-a-share-data-service-data`

CNEquity is the authoritative database foundation. The existing store contains:

- `curated/daily_bars`: 2,589 Parquet files and 10,756,825 rows according to
  Parquet metadata;
- latest physically present partition: `2026-08-28`;
- baseline promotion receipt: 2,580 files through `2026-08-17`;
- baseline published manifest hash:
  `dfc9229ef79bdb37f8e7ba3e7e59b6f44e857cb85c00295c1fdc7893e6f0f045`.

Nine incremental partitions from `2026-08-18` through `2026-08-28` are already
physically present. Their existing offline audit records 46,830 rows versus
46,872 requested rows: 42 requested symbol/date keys were not observed. The
audit deliberately does not classify those keys as suspension or true missing
bars. Its verdict is `NOT_CERTIFIED_FOR_PUBLICATION`:

`reports/implementation/QUERY_INCREMENTAL_PUBLICATION_AUDIT_V01.json`

`meta/state/daily_bars.json` has `last_success_trade_date=2026-08-28`. This is
ingestion progress metadata and is not a replacement for `latest_good_as_of` or
the published manifest.

## Query and MCP state

The query publication guard and tool-only MCP adapter are locally validated:

- `src/ashare_data/local_query.py`
- `tools/query_local_a_share.py`
- `src/ashare_data/mcp_server.py`
- `tools/serve_local_a_share_mcp.py`
- `tests/test_local_query.py`
- `tests/test_mcp_server.py`
- `requirements-mcp.txt`

The intended boundary is the exact verified 2,580-file published set. The nine
physical incremental files must stay excluded until their quality/publication
authority is resolved. MCP tools are limited to `bars`, `latest`, `instrument`,
and `status`; there is no arbitrary SQL, filesystem, provider, shell, or write
operation.

The parent completed review and fixes after the coding subagents stopped.
88 targeted tests pass. Actual HTTP initialization and all four tools pass;
the three stock latest queries take approximately 1.17–1.22 seconds each.
The 393-row 600519.SH date-range query took 1.10 seconds. Results cap at the
promoted 2026-08-17 baseline and report the physical 2026-08-28 boundary.

The MCP server runs as login LaunchAgent `io.asl.market-data-mcp` at
`http://127.0.0.1:8766/mcp`. ChatGPT has NOT been connected to it. Independent
audit and remote acceptance remain outstanding.

## Safety boundary

At this handoff:

- only the loopback query service is running; no provider job is running;
- no new provider request is authorized;
- no further canonical or checkpoint mutation is authorized;
- R4A9 remains unexecuted and unauthorized;
- the nine incremental partitions and their audit evidence must be preserved;
- do not replace the baseline manifest with the physical manifest merely to
  remove a drift error.

## Remaining work

1. Independently audit the exact implementation commit and publication guard.
2. Configure a private ChatGPT connection and record actual tool-call acceptance.
3. Keep the nine additional partitions excluded; any later certification is a
   separate bounded quality task, not a prerequisite for querying the baseline.

## Do not do during the pause

Do not delete, move, rename, rewrite, compact, or manually edit any Parquet
file. Do not rerun the nine-day catch-up, repair the 42 keys, promote a new
manifest, resume R4A9, or add preclose/trading-status/turnover/strategy logic.
