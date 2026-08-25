# R3 TDX VOLUME REBUILD INPUT MANIFEST - V01

DATE: 2026-08-25
BASE_HEAD: d716918658b9c3e7fa253fdaf4a01322d772aaa8
AUTH: AUTHOR_STATUS=PASS_PENDING_SOL_AUDIT

Companion JSON: `R3_TDX_VOLUME_REBUILD_INPUT_MANIFEST_V01.json` (one entry per
parquet file; `relative_path`, `file_size`, `sha256`).

## Provenance

- INPUT_ROOT: `/Users/luke808/AI/local-a-share-data-service-data/curated/daily_bars`
- INPUT_FILE_N: 2580
- INPUT_MANIFEST_HASH: `f9025a5cbc52d757fdc05d9e6ebb5f3c75c1cd93414a2f6c51bb83314594a6ec`
- An existing authoritative R3 manifest covering exactly this parquet set was
  NOT found; the deterministic read-only manifest above was created instead.

## Canonical serialization

`json.dumps(rows, ensure_ascii=True, sort_keys=True, separators=(',', ':'))`,
rows sorted by `relative_path`.  INPUT_MANIFEST_HASH is SHA-256 of those bytes.

## Participation

Every file under `curated/daily_bars/` participates in the TDX scan
(`pl.read_parquet("curated/daily_bars/**/*.parquet")`, partition
`trade_date=YYYY-MM-DD`, 2580 files, ~274 MiB).  No data modification.
