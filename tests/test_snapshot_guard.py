# Snapshot-date guard: a caller must not be able to declare a session.
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from cnequity.domain.snapshot_guard import (  # noqa: E402
    MIN_SESSION_COVERAGE,
    SETTLED_AFTER,
    SnapshotWindowError,
    assert_snapshot_matches_session,
    observe_snapshot,
)

SHANGHAI = ZoneInfo('Asia/Shanghai')
TARGET = date(2026, 9, 11)


def _epoch(moment: datetime) -> int:
    return int(moment.timestamp())


def _rows(stamp: datetime, *, n: int = 100, turnover: float = 1.5, zero_n: int = 0):
    rows = []
    for index in range(n):
        rows.append({
            'symbol': f'{index:06d}.SZ',
            'updated_at': _epoch(stamp),
            'turnover_rate': 0.0 if index < zero_n else turnover,
        })
    return rows


def _settled() -> datetime:
    return datetime(2026, 9, 11, 15, 34, tzinfo=SHANGHAI)


def _after_close() -> datetime:
    return datetime(2026, 9, 11, 16, 0, tzinfo=SHANGHAI)


class _FrozenDatetime(datetime):
    """A datetime whose ``now`` is pinned, so a clock-dependent guard is testable."""

    @classmethod
    def now(cls, tz=None):  # noqa: D102 - mirrors datetime.now
        return _after_close().astimezone(tz) if tz else _after_close()


# ------------------------------------------------------- session resolution --
def test_the_session_is_resolved_from_the_vendor_stamp():
    observation = observe_snapshot(_rows(_settled()))
    assert observation.session_date == TARGET
    assert observation.latest_update == _settled()
    assert observation.turnover_present_n == 100
    assert observation.turnover_zero_n == 0
    assert observation.is_settled is True


def test_the_latest_stamp_wins_over_a_stale_majority():
    # A page mixes long-halted rows carrying an old stamp with live ones.
    rows = _rows(datetime(2026, 9, 11, 8, 0, tzinfo=SHANGHAI), n=90)
    rows += _rows(_settled(), n=10)
    observation = observe_snapshot(rows)
    assert observation.latest_update == _settled()
    assert observation.session_date == TARGET
    # Coverage is measured on the resolved *date*, not the minute: rows that
    # merely stopped updating earlier the same session still belong to it.
    assert observation.session_coverage == pytest.approx(1.0, abs=0.001)


def test_raw_vendor_field_names_are_accepted_too():
    raw = [{'symbol': 'x', 'f124': _epoch(_settled()), 'f8': 1.5} for _ in range(10)]
    observation = observe_snapshot(raw)
    assert observation.session_date == TARGET
    assert observation.turnover_present_n == 10


# -------------------------------------------------------------- fail closed --
def test_an_unstamped_payload_is_refused():
    with pytest.raises(SnapshotWindowError) as error:
        assert_snapshot_matches_session([{'symbol': 'x'}], TARGET, now=_after_close())
    assert error.value.code == 'SNAPSHOT_UNSTAMPED'


def test_a_wrong_session_is_refused():
    # The exact forgery the previous round shipped: requesting one date while the
    # payload describes another.
    stamp = datetime(2026, 9, 10, 15, 34, tzinfo=SHANGHAI)
    with pytest.raises(SnapshotWindowError) as error:
        assert_snapshot_matches_session(_rows(stamp), TARGET, now=_after_close())
    assert error.value.code == 'SNAPSHOT_SESSION_MISMATCH'


def test_a_future_session_is_refused():
    stamp = datetime(2026, 9, 14, 15, 34, tzinfo=SHANGHAI)
    with pytest.raises(SnapshotWindowError) as error:
        assert_snapshot_matches_session(_rows(stamp), TARGET, now=_after_close())
    assert error.value.code == 'SNAPSHOT_SESSION_MISMATCH'


def test_an_intraday_capture_is_refused():
    # Stamped today, but the session had not finished happening.
    stamp = datetime(2026, 9, 11, 11, 0, tzinfo=SHANGHAI)
    with pytest.raises(SnapshotWindowError) as error:
        assert_snapshot_matches_session(_rows(stamp), TARGET, now=_after_close())
    assert error.value.code == 'SNAPSHOT_NOT_SETTLED'


def test_a_settled_payload_before_the_local_cutoff_is_refused():
    # Defends a clock-skewed or replayed run: settled data exists, but the
    # machine's own time says the window has not opened.
    now = datetime(2026, 9, 11, 12, 30, tzinfo=SHANGHAI)
    with pytest.raises(SnapshotWindowError) as error:
        assert_snapshot_matches_session(_rows(_settled()), TARGET, now=now)
    assert error.value.code == 'SNAPSHOT_WINDOW_NOT_OPEN'


def test_a_whole_market_zero_turnover_is_refused():
    # The next-session reset: correct date, settled stamp, all values zero.
    with pytest.raises(SnapshotWindowError) as error:
        assert_snapshot_matches_session(
            _rows(_settled(), n=100, zero_n=100), TARGET, now=_after_close())
    assert error.value.code == 'SNAPSHOT_TURNOVER_RESET'


def test_a_reset_just_past_the_threshold_is_still_refused():
    with pytest.raises(SnapshotWindowError) as error:
        assert_snapshot_matches_session(
            _rows(_settled(), n=100, zero_n=60), TARGET, now=_after_close())
    assert error.value.code == 'SNAPSHOT_TURNOVER_RESET'


def test_an_incoherent_page_is_refused():
    # Half the stamps belong to another session: not one observation.
    rows = _rows(_settled(), n=10)
    rows += _rows(datetime(2026, 9, 10, 15, 34, tzinfo=SHANGHAI), n=480)
    with pytest.raises(SnapshotWindowError) as error:
        assert_snapshot_matches_session(rows, TARGET, now=_after_close())
    assert error.value.code == 'SNAPSHOT_SESSION_INCOHERENT'


def test_a_payload_without_turnover_is_refused():
    rows = [{'symbol': 'x', 'updated_at': _epoch(_settled())}] * 10
    with pytest.raises(SnapshotWindowError) as error:
        assert_snapshot_matches_session(rows, TARGET, now=_after_close())
    assert error.value.code == 'SNAPSHOT_TURNOVER_ABSENT'


# ------------------------------------------------------------- happy paths --
def test_a_settled_payload_for_the_target_session_passes():
    observation = assert_snapshot_matches_session(_rows(_settled()), TARGET, now=_after_close())
    assert observation.session_date == TARGET
    assert observation.is_settled is True


def test_a_suspended_symbols_zero_does_not_trip_the_reset_gate():
    # Real suspensions publish a legitimate zero; the gate is about the whole
    # cross-section collapsing, not about individual halted names.
    observation = assert_snapshot_matches_session(
        _rows(_settled(), n=100, zero_n=20), TARGET, now=_after_close())
    assert observation.turnover_zero_n == 20


def test_settle_enforcement_can_be_relaxed_but_the_date_check_cannot():
    stamp = datetime(2026, 9, 11, 11, 0, tzinfo=SHANGHAI)
    # Explicitly opting out of the settle requirement still keeps the date check.
    observation = assert_snapshot_matches_session(
        _rows(stamp), TARGET, now=_after_close(), require_settled=False)
    assert observation.session_date == TARGET
    with pytest.raises(SnapshotWindowError) as error:
        assert_snapshot_matches_session(
            _rows(datetime(2026, 9, 10, 15, 0, tzinfo=SHANGHAI)), TARGET,
            now=_after_close(), require_settled=False)
    assert error.value.code == 'SNAPSHOT_SESSION_MISMATCH'


def test_the_cutoff_is_the_published_close_not_midnight():
    assert SETTLED_AFTER.hour == 15
    assert MIN_SESSION_COVERAGE > 0.5


# -------------------------------------------- adapter integration (offline) --
def test_adapter_requests_the_stamp_and_enforces_the_guard():
    import inspect
    from cnequity.adapters.eastmoney import valuation

    assert 'f124' in valuation._VALUATION_FIELDS
    source = inspect.getsource(valuation.fetch_valuation_metrics)
    assert 'assert_snapshot_matches_session' in source
    assert 'item.get("f124")' in source


def test_adapter_guard_uses_the_requested_trade_date():
    import inspect
    from cnequity.adapters.eastmoney import valuation

    source = inspect.getsource(valuation.fetch_valuation_metrics)
    assert 'assert_snapshot_matches_session(rows, trade_date)' in source


def test_adapter_fails_closed_on_a_synthetic_wrong_session():
    # Drive the real adapter body with a stubbed page walker, so the guard is
    # exercised through the call path rather than only in isolation.
    from cnequity.adapters.eastmoney import valuation

    stamp = _epoch(datetime(2026, 9, 10, 15, 34, tzinfo=SHANGHAI))
    fake = [{'f12': f'{i:06d}', 'f13': 0, 'f8': 1.0, 'f9': 1.0, 'f23': 1.0,
             'f45': 1.0, 'f20': 1.0, 'f21': 1.0, 'f124': stamp} for i in range(50)]
    original = valuation.fetch_clist_pages
    valuation.fetch_clist_pages = lambda *a, **k: fake
    try:
        with pytest.raises(SnapshotWindowError) as error:
            valuation.fetch_valuation_metrics(TARGET)
    finally:
        valuation.fetch_clist_pages = original
    assert error.value.code == 'SNAPSHOT_SESSION_MISMATCH'


def test_adapter_returns_rows_when_the_session_matches():
    from cnequity.adapters.eastmoney import valuation
    from cnequity.domain import snapshot_guard

    stamp = _epoch(_settled())
    fake = [{'f12': f'{i:06d}', 'f13': 0, 'f8': 1.0, 'f9': 1.0, 'f23': 1.0,
             'f45': 1.0, 'f20': 1.0, 'f21': 1.0, 'f124': stamp} for i in range(50)]
    original = valuation.fetch_clist_pages
    original_datetime = snapshot_guard.datetime
    # The adapter calls the guard with the real clock, so freeze it: otherwise
    # this test would pass or fail depending on when it is run.
    snapshot_guard.datetime = _FrozenDatetime
    valuation.fetch_clist_pages = lambda *a, **k: fake
    try:
        frame = valuation.fetch_valuation_metrics(TARGET)
    finally:
        valuation.fetch_clist_pages = original
        snapshot_guard.datetime = original_datetime
    assert frame.height == 50
    assert 'turnover_rate' in frame.columns
