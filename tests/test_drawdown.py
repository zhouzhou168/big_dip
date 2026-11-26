"""回撤公式和窗口切分。不访问网络。"""

from __future__ import annotations

import datetime as dt

import pytest

from big_dip.assets import ASSETS, Market, last_allowed_date
from big_dip.drawdown import Bar, analyze, shift_years, slice_window
from big_dip.monitor import prepare_bars


def _bars(pairs: list[tuple[str, float]]) -> list[Bar]:
    return [Bar(dt.date.fromisoformat(day), close) for day, close in pairs]


def test_drawdown_finds_peak_trough_and_recovery():
    bars = _bars(
        [
            ("2020-01-01", 100),
            ("2020-01-02", 120),
            ("2020-01-03", 90),
            ("2020-01-04", 80),
            ("2020-01-05", 100),
            ("2020-01-06", 130),
            ("2020-01-07", 110),
        ]
    )
    stats = analyze(bars)
    assert stats.ath == 130
    assert stats.ath_date == dt.date(2020, 1, 6)
    assert stats.current_dd == pytest.approx(110 / 130 - 1)
    assert stats.mdd == pytest.approx(80 / 120 - 1)
    assert stats.mdd_peak_date == dt.date(2020, 1, 2)
    assert stats.mdd_peak == 120
    assert stats.mdd_trough_date == dt.date(2020, 1, 4)
    assert stats.mdd_trough == 80
    assert stats.recovered is True
    assert stats.recovery_date == dt.date(2020, 1, 6)


def test_later_equal_high_does_not_rewrite_an_earlier_drawdown():
    bars = _bars(
        [
            ("2020-01-01", 100),
            ("2020-01-02", 40),
            ("2020-01-03", 100),
            ("2020-01-04", 80),
        ]
    )
    stats = analyze(bars)
    assert stats.mdd == pytest.approx(40 / 100 - 1)
    assert stats.mdd_peak_date == dt.date(2020, 1, 1)
    assert stats.mdd_trough_date == dt.date(2020, 1, 2)
    assert stats.recovered is True
    assert stats.recovery_date == dt.date(2020, 1, 3)
    assert stats.current_dd == pytest.approx(80 / 100 - 1)
    assert stats.ath_date == dt.date(2020, 1, 1)


def test_unrecovered_drawdown():
    bars = _bars(
        [
            ("2020-01-01", 5),
            ("2020-01-02", 8),
            ("2020-01-03", 4),
            ("2020-01-04", 6),
        ]
    )
    stats = analyze(bars)
    assert stats.mdd == pytest.approx(4 / 8 - 1)
    assert stats.mdd_peak_date == dt.date(2020, 1, 2)
    assert stats.mdd_trough_date == dt.date(2020, 1, 3)
    assert stats.recovered is False
    assert stats.recovery_date is None


def test_touching_the_peak_moves_the_running_peak_date():
    bars = _bars(
        [
            ("2020-01-01", 10),
            ("2020-01-02", 10),
            ("2020-01-03", 7),
        ]
    )
    stats = analyze(bars)
    assert stats.mdd_peak_date == dt.date(2020, 1, 2)
    assert stats.mdd == pytest.approx(7 / 10 - 1)
    assert stats.ath_date == dt.date(2020, 1, 1)


def test_flat_series_has_no_drawdown():
    bars = _bars([("2020-01-01", 3), ("2020-01-02", 3)])
    stats = analyze(bars)
    assert stats.mdd == 0
    assert stats.current_dd == 0
    assert stats.recovered is True


def test_empty_and_non_positive_prices_are_rejected():
    with pytest.raises(ValueError):
        analyze([])
    with pytest.raises(ValueError):
        analyze(_bars([("2020-01-01", 1), ("2020-01-02", 0)]))


def test_secondary_window_does_not_import_the_prior_high():
    bars = _bars(
        [
            ("2020-01-02", 100),
            ("2020-06-01", 50),
            ("2026-01-05", 80),
            ("2026-06-01", 70),
        ]
    )
    anchor = bars[-1].date
    ytd = slice_window(bars, "ytd", anchor)
    stats = analyze(ytd)
    assert [bar.date for bar in ytd] == [dt.date(2026, 1, 5), dt.date(2026, 6, 1)]
    assert stats.mdd == pytest.approx(70 / 80 - 1)
    assert stats.ath == 80
    inception = analyze(slice_window(bars, "inception", anchor))
    assert inception.mdd == pytest.approx(50 / 100 - 1)


def test_year_windows_follow_the_last_session():
    bars = _bars(
        [
            ("2021-11-26", 10),
            ("2024-02-28", 12),
            ("2024-02-29", 9),
            ("2026-11-26", 11),
        ]
    )
    anchor = dt.date(2026, 11, 26)
    y1 = slice_window(bars, "y1", anchor)
    y5 = slice_window(bars, "y5", anchor)
    assert y1 == [bars[-1]]
    assert y5[0].date == dt.date(2021, 11, 26)
    modern = slice_window(
        _bars([("1957-03-01", 40), ("1957-03-04", 44), ("2026-11-25", 70)]),
        "modern1957",
        dt.date(2026, 11, 25),
    )
    assert modern[0].date == dt.date(1957, 3, 4)


def test_shift_years_on_leap_day():
    assert shift_years(dt.date(2024, 2, 29), -1) == dt.date(2023, 2, 28)
    assert shift_years(dt.date(2024, 2, 29), -4) == dt.date(2020, 2, 29)


def test_session_cutoff_matches_a_late_shanghai_afternoon():
    # 2026-11-26 12:50 UTC = 上海 20:50，美东 07:50，UTC 日尚未结束。
    now = dt.datetime(2026, 11, 26, 12, 50, tzinfo=dt.timezone.utc)
    assert last_allowed_date(Market.CN, now) == dt.date(2026, 11, 26)
    assert last_allowed_date(Market.US, now) == dt.date(2026, 11, 25)
    assert last_allowed_date(Market.CRYPTO, now) == dt.date(2026, 11, 25)


def test_session_cutoff_before_the_a_share_close():
    now = dt.datetime(2026, 11, 26, 2, 0, tzinfo=dt.timezone.utc)  # 上海 10:00
    assert last_allowed_date(Market.CN, now) == dt.date(2026, 11, 25)
    assert last_allowed_date(Market.US, now) == dt.date(2026, 11, 25)


def test_prepare_bars_drops_shenzhen_prehistory_and_unfinished_sessions():
    asset = next(item for item in ASSETS if item.key == "szse")
    now = dt.datetime(2026, 11, 26, 12, 50, tzinfo=dt.timezone.utc)
    bars = _bars(
        [
            ("1991-04-03", 988.05),
            ("1994-07-19", 1200),
            ("1994-07-20", 1000),
            ("2026-11-26", 10084),
            ("2026-11-27", 10100),
        ]
    )
    kept = prepare_bars(bars, asset, now)
    assert [bar.date.isoformat() for bar in kept] == ["1994-07-20", "2026-11-26"]


def test_prepare_bars_drops_an_unfinished_crypto_day():
    asset = next(item for item in ASSETS if item.key == "btc")
    now = dt.datetime(2026, 11, 26, 12, 50, tzinfo=dt.timezone.utc)
    bars = _bars(
        [
            ("2010-07-17", 0.05),
            ("2010-07-18", 0.09),
            ("2026-11-25", 100),
            ("2026-11-26", 101),
        ]
    )
    kept = prepare_bars(bars, asset, now)
    assert [bar.date.isoformat() for bar in kept] == ["2010-07-18", "2026-11-25"]
