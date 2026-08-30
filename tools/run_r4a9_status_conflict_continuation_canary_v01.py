#!/usr/bin/env python3
"""Freeze the membership of one R4A9 continuation canary invocation.

The previous canary selected the normal control from the mutable UNVISITED
set on every invocation.  This module makes the execution membership an
immutable, receipt-bindable plan.  It contains no provider import and does
not execute a canary by itself; a future runner supplies the provider/unit
callback after all local authority gates have passed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Callable


TASK = "R4A9_STATUS_CONFLICT_FULL_CONTINUATION_CANARY_V01"
CANARY_PLAN_VERSION = "R4A9_STATUS_CONFLICT_CANARY_PLAN_V02"
OVERLAY_SYMBOLS = ("600647.SH", "600766.SH", "603133.SH")


class CanaryScopeError(RuntimeError):
    """Raised before a unit/provider callback can escape the frozen scope."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canary_symbol_hash(symbols: list[str] | tuple[str, ...]) -> str:
    """Hash the ordered execution membership, not a mutable checkpoint set."""

    return sha256_json(list(symbols))


def _require(condition: bool, code: str, detail: Any | None = None) -> None:
    if not condition:
        suffix = "" if detail is None else f":{detail}"
        raise CanaryScopeError(f"{code}{suffix}")


def _initial_unvisited_symbols(checkpoint: dict[str, Any]) -> tuple[str, ...]:
    units = checkpoint.get("units")
    _require(isinstance(units, dict), "CHECKPOINT_UNITS_INVALID")
    return tuple(
        sorted(
            str(symbol)
            for symbol, entry in units.items()
            if isinstance(entry, dict) and entry.get("STATE") == "UNVISITED"
        )
    )


@dataclass(frozen=True)
class CanaryExecutionPlan:
    """The immutable membership and authority binding for one invocation."""

    start_checkpoint_hash: str
    initial_unvisited_symbols: tuple[str, ...]
    start_unvisited_set_hash: str
    daily_input_manifest_hash: str
    adapter_authority_sha: str
    overlay_keyset_hash: str
    ordered_canary_symbols: tuple[str, ...]
    normal_control_symbol: str
    plan_hash: str

    def binding(self) -> dict[str, Any]:
        """Return the compact contract carried by phase/checkpoint receipts."""

        return {
            "CANARY_PLAN_VERSION": CANARY_PLAN_VERSION,
            "START_CHECKPOINT_HASH": self.start_checkpoint_hash,
            "START_UNVISITED_SET_HASH": self.start_unvisited_set_hash,
            "DAILY_INPUT_MANIFEST_HASH": self.daily_input_manifest_hash,
            "ADAPTER_AUTHORITY_SHA": self.adapter_authority_sha,
            "OVERLAY_KEYSET_HASH": self.overlay_keyset_hash,
            "ORDERED_CANARY_SYMBOLS": list(self.ordered_canary_symbols),
            "NORMAL_CONTROL_SYMBOL": self.normal_control_symbol,
            "CANARY_SYMBOL_HASH": canary_symbol_hash(self.ordered_canary_symbols),
            "CANARY_PLAN_HASH": self.plan_hash,
        }

    def phase_binding(self, phase_index: int, authorized_symbol: str) -> dict[str, Any]:
        enforce_requested_symbol(self, authorized_symbol)
        _require(phase_index >= 1, "PHASE_INDEX_INVALID", phase_index)
        _require(
            self.ordered_canary_symbols[phase_index - 1] == authorized_symbol,
            "PHASE_SYMBOL_MISMATCH",
            authorized_symbol,
        )
        return {
            "CANARY_PLAN_HASH": self.plan_hash,
            "PHASE_INDEX": phase_index,
            "AUTHORIZED_SYMBOL": authorized_symbol,
        }


def freeze_canary_plan(
    checkpoint: dict[str, Any],
    *,
    start_checkpoint_hash: str,
    daily_input_manifest_hash: str,
    adapter_authority_sha: str,
    overlay_keyset_hash: str,
    overlay_symbols: tuple[str, ...] = OVERLAY_SYMBOLS,
) -> CanaryExecutionPlan:
    """Capture scope once, before any provider request or checkpoint mutation."""

    initial_unvisited = _initial_unvisited_symbols(checkpoint)
    overlays = tuple(overlay_symbols)
    _require(len(overlays) == len(set(overlays)), "OVERLAY_SYMBOL_DUPLICATE")
    _require(
        all(symbol in initial_unvisited for symbol in overlays),
        "OVERLAY_CANARY_NOT_IN_INITIAL_UNVISITED",
    )
    controls = tuple(symbol for symbol in initial_unvisited if symbol not in overlays)
    _require(controls, "NORMAL_CONTROL_UNAVAILABLE")
    normal_control = controls[0]
    ordered = (normal_control, *overlays)
    _require(len(ordered) == 4 and len(set(ordered)) == 4, "CANARY_SCOPE_NOT_EXACTLY_FOUR")

    start_unvisited_set_hash = sha256_json(list(initial_unvisited))
    plan_payload = {
        "CANARY_PLAN_VERSION": CANARY_PLAN_VERSION,
        "START_CHECKPOINT_HASH": start_checkpoint_hash,
        "START_UNVISITED_SET_HASH": start_unvisited_set_hash,
        "DAILY_INPUT_MANIFEST_HASH": daily_input_manifest_hash,
        "ADAPTER_AUTHORITY_SHA": adapter_authority_sha,
        "OVERLAY_KEYSET_HASH": overlay_keyset_hash,
        "ORDERED_CANARY_SYMBOLS": list(ordered),
    }
    return CanaryExecutionPlan(
        start_checkpoint_hash=start_checkpoint_hash,
        initial_unvisited_symbols=initial_unvisited,
        start_unvisited_set_hash=start_unvisited_set_hash,
        daily_input_manifest_hash=daily_input_manifest_hash,
        adapter_authority_sha=adapter_authority_sha,
        overlay_keyset_hash=overlay_keyset_hash,
        ordered_canary_symbols=ordered,
        normal_control_symbol=normal_control,
        plan_hash=sha256_json(plan_payload),
    )


def enforce_requested_symbol(plan: CanaryExecutionPlan, requested_symbol: str) -> None:
    """Run immediately before provider login/request or any unit callback."""

    _require(
        requested_symbol in plan.ordered_canary_symbols,
        "FROZEN_CANARY_SYMBOL_SCOPE_VIOLATION",
        requested_symbol,
    )


def guarded_provider_request(
    plan: CanaryExecutionPlan,
    requested_symbol: str,
    provider_call: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Guard a provider callback before it can perform external work."""

    enforce_requested_symbol(plan, requested_symbol)
    return provider_call(*args, **kwargs)


def execute_frozen_plan(
    plan: CanaryExecutionPlan,
    checkpoint: dict[str, Any],
    run_unit: Callable[[str, int, CanaryExecutionPlan], Any],
) -> list[dict[str, Any]]:
    """Drive a fixed plan while allowing checkpoint state to advance.

    ``run_unit`` owns the existing audited executor.  This helper never
    selects from the current checkpoint after the plan is created; it only
    consults the frozen four-symbol tuple.  A COMPLETE unit is reported as a
    resume skip, while an UNVISITED unit is delegated after the scope guard.
    """

    units = checkpoint.get("units")
    _require(isinstance(units, dict), "CHECKPOINT_UNITS_INVALID")
    outcomes: list[dict[str, Any]] = []
    for phase_index, symbol in enumerate(plan.ordered_canary_symbols, start=1):
        enforce_requested_symbol(plan, symbol)
        entry = units.get(symbol)
        _require(isinstance(entry, dict), "CANARY_SYMBOL_MISSING", symbol)
        state = entry.get("STATE")
        if state == "COMPLETE":
            outcomes.append(
                {
                    "symbol": symbol,
                    "phase_index": phase_index,
                    "status": "SKIPPED_VERIFIED_COMPLETE",
                    "resumed_complete": True,
                    **plan.phase_binding(phase_index, symbol),
                }
            )
            continue
        _require(state == "UNVISITED", "NON_UNVISITED_EXECUTION_ATTEMPT", symbol)
        result = run_unit(symbol, phase_index, plan)
        outcomes.append(
            {
                "symbol": symbol,
                "phase_index": phase_index,
                "status": "EXECUTED",
                "resumed_complete": False,
                "result": result,
                **plan.phase_binding(phase_index, symbol),
            }
        )
    return outcomes


__all__ = [
    "CANARY_PLAN_VERSION",
    "CanaryExecutionPlan",
    "CanaryScopeError",
    "OVERLAY_SYMBOLS",
    "canary_symbol_hash",
    "canonical_json_bytes",
    "enforce_requested_symbol",
    "execute_frozen_plan",
    "freeze_canary_plan",
    "guarded_provider_request",
    "sha256_json",
]
