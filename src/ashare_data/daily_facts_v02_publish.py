"""Offline, exact-date V02 certification and existing-envelope publication.

This adds a certified date to the existing independent Facts authority. It
does not replace old V1 files or set global readiness, and never uses the V1
rows as candidate inputs. All source files are bound before and after build.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ashare_data.daily_facts_v02 import (
    SCHEMA, RULE_PCT_CHG, RULE_TURNOVER, build_shadow_rows, canonical_sha,
    derive_pct_chg, display_reference_price, load_sources, r3_bars,
)


def certify_rows(rows, expected, day, bundle, bars):
    """Account for every frozen key and check actual values against sources."""
    counts = Counter({k: 0 for k in (
        "MISSING_PK_N", "EXTRA_PK_N", "DUPLICATE_PK_N", "INVALID_ROW_N",
        "PRECLOSE_UNRESOLVED_N", "PRECLOSE_MISMATCH_N", "PCT_CHG_MISMATCH_N",
        "TURNOVER_MISMATCH_N", "STATUS_MISMATCH_N", "TRADE_STATUS_CONFLICT_N",
    )})
    keys = [(r.get("symbol"), r.get("trade_date")) for r in rows]
    wanted = {(s, day) for s in expected}
    counts["MISSING_PK_N"] = len(wanted - set(keys))
    counts["EXTRA_PK_N"] = len(set(keys) - wanted)
    counts["DUPLICATE_PK_N"] = len(keys) - len(set(keys))

    def finite(value):
        return type(value) in (int, float) and math.isfinite(value)

    prior_cache = {}
    for row in rows:
        key = (row.get("symbol"), day)
        symbol = key[0]
        status = bundle.trading_status.get(key, {})
        trade = row.get("trade_status")
        st = row.get("is_st")
        bar = bars.get(symbol, {})
        invalid = (row.get("schema_version") != SCHEMA or row.get("trade_date") != day
                   or row.get("quality_status") != "PASS" or bool(row.get("blockers"))
                   or trade not in {"TRADING", "SUSPENDED"} or st not in {"TRUE", "FALSE"}
                   or row.get("turnover_unit") != "PERCENT")
        counts["INVALID_ROW_N"] += int(invalid)
        expected_trade = {"normal": "TRADING", "suspended": "SUSPENDED"}.get(status.get("status"))
        expected_st = {True: "TRUE", False: "FALSE"}.get(status.get("risk_warning"))
        counts["STATUS_MISMATCH_N"] += int(
            trade != expected_trade or st != expected_st or status.get("source") != "eastmoney")
        preclose = row.get("preclose")
        if not finite(preclose) or preclose <= 0:
            counts["PRECLOSE_UNRESOLVED_N"] += 1
        else:
            event = bundle.corporate_actions.get(key)
            evidence = bundle.reference_price_evidence.get(key)
            if event and not evidence:
                counts["PRECLOSE_UNRESOLVED_N"] += 1
            prior_day = row.get("prior_close_session")
            if not prior_day or prior_day >= day:
                counts["PRECLOSE_MISMATCH_N"] += 1
            else:
                if prior_day not in prior_cache:
                    prior_cache[prior_day] = r3_bars(bundle, prior_day)
                prior = prior_cache[prior_day].get(symbol, {}).get("close")
                if not finite(prior):
                    counts["PRECLOSE_MISMATCH_N"] += 1
                elif evidence:
                    action = event or {}
                    reference = float(display_reference_price(prior,
                        cash_dividend=evidence.effective_cash_dividend_per_share,
                        **{k: action.get(k) or 0 for k in (
                            "bonus_ratio", "transfer_ratio", "allotment_ratio", "allotment_price")}))
                    counts["PRECLOSE_MISMATCH_N"] += int(
                        abs(preclose-reference) > 1e-9
                        or row.get("reference_price_source_hash") != evidence.source_hash
                        or row.get("reference_price_announcement_id") != evidence.announcement_id
                        or row.get("preclose_rule_id") != "EXCHANGE_REFERENCE_PRICE_V02")
                else:
                    counts["PRECLOSE_MISMATCH_N"] += int(abs(preclose-prior) > 1e-9
                        or row.get("preclose_rule_id") != "PRIOR_PUBLISHED_CLOSE_V02")
        if trade == "SUSPENDED":
            counts["TRADE_STATUS_CONFLICT_N"] += int(bar.get("volume") != 0 or bar.get("amount") != 0)
            counts["PCT_CHG_MISMATCH_N"] += int(row.get("pct_chg") is not None)
            counts["TURNOVER_MISMATCH_N"] += int(row.get("turnover_rate") is not None)
        else:
            counts["TRADE_STATUS_CONFLICT_N"] += int(not finite(bar.get("volume")) or bar.get("volume", 0) <= 0)
            pct = row.get("pct_chg")
            if finite(preclose) and preclose > 0 and finite(bar.get("close")):
                counts["PCT_CHG_MISMATCH_N"] += int(
                    not finite(pct) or abs(pct-derive_pct_chg(bar["close"], preclose)) > 1e-9
                    or row.get("pct_chg_rule_id") != RULE_PCT_CHG)
            else:
                counts["PCT_CHG_MISMATCH_N"] += 1
            turnover = row.get("turnover_rate")
            expected_turnover = bundle.turnover_rate.get(key)
            counts["TURNOVER_MISMATCH_N"] += int(
                not finite(turnover) or turnover < 0 or turnover != expected_turnover
                or row.get("turnover_rate_rule_id") != RULE_TURNOVER)
    return {"PASS": not any(counts.values()), "expected_key_n": len(wanted),
            "row_n": len(rows), "counters": dict(counts)}


def publication_frame(rows, r3_manifest_hash):
    import polars as pl
    frame = pl.DataFrame(rows, infer_schema_length=None).with_columns(
        pl.col("trade_date").str.to_date(),
        pl.col("blockers").cast(pl.String),
        *[pl.col(f).cast(pl.Float64) for f in ("preclose", "pct_chg", "turnover_rate")],
        pl.lit(datetime.now(timezone.utc).isoformat()).alias("computed_at"),
        pl.lit(r3_manifest_hash).alias("r3_daily_manifest_hash"),
    )
    if set().union(*(r.keys() for r in rows)) - set(frame.columns):
        raise ValueError("CANDIDATE_COLUMNS_DROPPED")
    return frame


def publish(root: Path, *, day: str, stage: Path, evidence: Path, execute=False, replace_manifest_hash=None):
    import polars as pl
    from ashare_data.local_query import LocalQuery
    # These existing R3 primitives retain the single writer and atomic JSON rules.
    from run_r3_frozen_shsz_incremental_v01 import (
        ROOT, atomic_json, eligible_symbols, frozen_authority, sha256_file, writer_lock,
    )
    from run_daily_facts_v02_shadow_v01 import load_calendar, load_lifecycle

    root, stage, evidence = root.resolve(), stage.resolve(), evidence.resolve()
    if root != ROOT.resolve() or not stage.is_relative_to(root / "staging"):
        raise ValueError("INVALID_PUBLICATION_ROOT")
    target = date.fromisoformat(day)
    with writer_lock():
        with LocalQuery(root) as query:
            baseline = query.status()
        if baseline["DAILY_PUBLISHED_AS_OF"] != day:
            raise ValueError("R3_TARGET_NOT_PUBLISHED")
        _, records = frozen_authority()
        expected = eligible_symbols(day, records)
        pointer_path = root / "meta/asl/daily_facts/published-daily-facts-authority.json"
        pointer_bytes = pointer_path.read_bytes()
        pointer = json.loads(pointer_bytes)
        prior_files = json.loads((root / pointer["plan"]).read_text())["manifest"]["files"]
        same_date = [f for f in prior_files if f"trade_date={day}/" in f["relative_path"]]
        filename = "part-full-eligible-v02.parquet"
        if replace_manifest_hash is not None:
            if pointer["manifest_hash"] != replace_manifest_hash or len(same_date) != 1:
                raise ValueError("REPAIR_AUTHORITY_MISMATCH")
            if "v02" not in same_date[0]["relative_path"]:
                raise ValueError("REPAIR_REQUIRES_V02_PARTITION")
            prior_files = [f for f in prior_files if f not in same_date]
            filename = f"part-full-eligible-v02-repair-{replace_manifest_hash[:12]}.parquet"
        elif same_date:
            raise ValueError("FACT_DATE_ALREADY_EXISTS")
        target_path = root / "curated/daily_facts" / f"trade_date={day}" / filename
        if target_path.exists():
            raise ValueError("FACT_FILE_ALREADY_EXISTS")

        def input_records():
            paths = [evidence]
            paths.extend(root / r["source_relative_path"]
                         for r in json.loads(evidence.read_text())["records"])
            for dataset in ("trading_status", "valuation_metrics", "corporate_actions"):
                paths.extend(sorted((root / "curated" / dataset).rglob("*.parquet")))
            return [{"relative_path": str(p.relative_to(root)), "sha256": sha256_file(p),
                     "file_size": p.stat().st_size} for p in sorted(paths)]

        before = input_records()
        bundle = load_sources(root, evidence_path=evidence)
        # The committed CNEquity sources must not contain duplicate same-date keys.
        for files in (bundle.trading_status_files, bundle.valuation_files):
            frame = pl.read_parquet(list(files)).filter(pl.col("trade_date") == target)
            if frame.height != frame.unique(["symbol", "trade_date"]).height:
                raise ValueError("DUPLICATE_SOURCE_KEYS")
        action_frame = pl.read_parquet(list(bundle.corporate_actions_files)).filter(pl.col("ex_date") == target)
        if action_frame.height != action_frame.unique(["symbol", "ex_date"]).height:
            raise ValueError("MULTIPLE_ACTIONS_REQUIRE_EXPLICIT_AGGREGATION")
        sessions = [d.isoformat() for d in load_calendar(root) if d < target]
        rows, report = build_shadow_rows(bundle, day, max(sessions),
            lifecycle=load_lifecycle(root), prior_days=list(reversed(sorted(sessions)))[:12])
        quality = certify_rows(rows, expected, day, bundle, r3_bars(bundle, day))
        quality["generation_report"] = report
        quality["input_files"] = before
        quality["r3_daily_manifest_hash"] = bundle.r3_manifest_hash
        quality["source_network_requests_during_certification"] = 0
        if before != input_records():
            raise ValueError("INPUT_CHANGED_DURING_CERTIFICATION")
        stage.mkdir(parents=True, exist_ok=True)
        atomic_json(stage / "certification_receipt.json", quality)
        if not quality["PASS"]:
            return {"status": "BLOCKED_CERTIFICATION", **quality}
        frame = publication_frame(rows, bundle.r3_manifest_hash)
        candidate = stage / "candidate-v02.parquet"
        frame.write_parquet(candidate)
        reread = pl.read_parquet(candidate)
        if not reread.equals(frame) or reread.height != len(expected) or any(reread.schema[f] != pl.Float64 for f in ("preclose", "pct_chg", "turnover_rate")):
            raise ValueError("CANDIDATE_READBACK_FAILED")
        # Exercise the actual query engine across old and candidate schemas
        # before publication, including optional metadata and Python conversion.
        import duckdb
        with duckdb.connect(":memory:") as con:
            query_paths = [str(root / f["relative_path"]) for f in prior_files] + [str(candidate)]
            fetched = con.execute("select * from read_parquet(?, union_by_name=true) where trade_date=?",
                                  [query_paths, target]).fetchall()
            if len(fetched) != len(expected):
                raise ValueError("QUERY_CANDIDATE_READBACK_FAILED")
            probe_symbols = {r["symbol"] for r in rows[::128]}
            probe_symbols.update(r["symbol"] for r in rows
                                 if r.get("reference_price_source_hash") or r["trade_status"] == "SUSPENDED")
            probe_symbols.add(rows[-1]["symbol"])
            for symbol in sorted(probe_symbols):
                result = con.execute("select * from read_parquet(?, union_by_name=true) where trade_date=? and symbol=?",
                                     [query_paths, target, symbol]).fetchall()
                if len(result) != 1:
                    raise ValueError("QUERY_KEY_READBACK_FAILED")
        if not execute:
            return {"status": "READY", **quality}
        with LocalQuery(root) as query:
            if query.status()["DAILY_MANIFEST_HASH"] != bundle.r3_manifest_hash:
                raise ValueError("R3_CHANGED_BEFORE_PROMOTION")
        if pointer_path.read_bytes() != pointer_bytes or before != input_records():
            raise ValueError("AUTHORITY_CHANGED_BEFORE_PROMOTION")
        record = {"relative_path": str(target_path.relative_to(root)), "sha256": sha256_file(candidate),
                  "file_size": candidate.stat().st_size}
        files = sorted([*prior_files, record], key=lambda p: p["relative_path"])
        manifest = {"schema": "ASL_DAILY_FACTS_MANIFEST_V01", "file_n": len(files),
                    "files": files, "manifest_hash": canonical_sha(files)}
        scope = day + "_FULL_ELIGIBLE_V02"
        plan = {"schema": "ASL_DAILY_FACTS_PUBLICATION_PLAN_V01", "scope": scope,
                "published_as_of": day, "facts_schema": SCHEMA, "manifest": manifest,
                "r3_daily_manifest_hash": bundle.r3_manifest_hash}
        receipt = {"schema": "ASL_DAILY_FACTS_PROMOTION_RECEIPT_V01", "STATE": "COMMITTED",
                   "status": "FULL_ELIGIBLE_ONE_DAY_PUBLISHED", "scope": scope,
                   "published_as_of": day, "facts_schema": SCHEMA, "manifest_hash": manifest["manifest_hash"],
                   "quality": quality, "prior_pointer": pointer, "provider_network_request_n": 0}
        # Evidence and a verified file precede the only authority mutation.
        atomic_json(stage / "promotion_plan.json", plan)
        atomic_json(stage / "promotion_receipt.json", receipt)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("xb") as handle:
            handle.write(candidate.read_bytes())
            handle.flush()
            import os
            os.fsync(handle.fileno())
        if sha256_file(target_path) != record["sha256"]:
            raise ValueError("INSTALLED_FACT_HASH_MISMATCH")
        atomic_json(pointer_path, {"schema": "ASL_PUBLISHED_DAILY_FACTS_AUTHORITY_V01",
            "plan": str((stage / "promotion_plan.json").relative_to(root)),
            "receipt": str((stage / "promotion_receipt.json").relative_to(root)),
            "manifest_hash": manifest["manifest_hash"]})
        return {"status": "PUBLISHED", "scope": scope, "manifest_hash": manifest["manifest_hash"],
                "row_n": len(rows), "quality": quality}
