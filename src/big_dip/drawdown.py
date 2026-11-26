"""日收盘回撤。窗口内的高点不会用到窗口开始之前的收盘。"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    date: dt.date
    close: float


@dataclass(frozen=True)
class DrawdownStats:
    n: int
    first_date: dt.date
    first_close: float
    last_date: dt.date
    last_close: float
    ath: float
    ath_date: dt.date
    current_dd: float
    mdd: float
    mdd_peak_date: dt.date
    mdd_peak: float
    mdd_trough_date: dt.date
    mdd_trough: float
    recovered: bool
    recovery_date: dt.date | None

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "first_date": self.first_date.isoformat(),
            "first_close": self.first_close,
            "last_date": self.last_date.isoformat(),
            "last_close": self.last_close,
            "ath": self.ath,
            "ath_date": self.ath_date.isoformat(),
            "current_dd": self.current_dd,
            "mdd": self.mdd,
            "mdd_peak_date": self.mdd_peak_date.isoformat(),
            "mdd_peak": self.mdd_peak,
            "mdd_trough_date": self.mdd_trough_date.isoformat(),
            "mdd_trough": self.mdd_trough,
            "recovered": self.recovered,
            "recovery_date": self.recovery_date.isoformat() if self.recovery_date else None,
        }


def shift_years(day: dt.date, years: int) -> dt.date:
    """按日历年平移。2 月 29 日落到非闰年时改为 2 月 28 日。"""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def slice_window(bars: list[Bar], kind: str, anchor: dt.date) -> list[Bar]:
    """切出窗口。anchor 是整段序列的最新收盘日，次要窗口不带入更早的高点。"""
    if kind == "inception":
        return list(bars)
    if kind == "ytd":
        start = dt.date(anchor.year, 1, 1)
    elif kind == "y1":
        start = shift_years(anchor, -1)
    elif kind == "y5":
        start = shift_years(anchor, -5)
    elif kind == "modern1957":
        start = dt.date(1957, 3, 4)
    else:
        raise ValueError(f"未知窗口 {kind}")
    return [bar for bar in bars if bar.date >= start]


def analyze(bars: list[Bar]) -> DrawdownStats:
    """计算一个窗口内的当前回撤和历史最大回撤。

    时点 t 的回撤 = close_t / 截至 t 的滚动最高收盘 - 1。
    滚动最高被打平或刷新时，峰值日期改到这一天；最大回撤记下谷值当时的峰值日期。
    收复日是谷值之后，收盘第一次回到该峰值或更高的日期。
    """
    if not bars:
        raise ValueError("没有可计算的收盘价")
    for bar in bars:
        if not math.isfinite(bar.close) or bar.close <= 0:
            raise ValueError(f"{bar.date.isoformat()} 的收盘价不是正数")

    ath_index = 0
    for index, bar in enumerate(bars):
        if bar.close > bars[ath_index].close:
            ath_index = index
    ath = bars[ath_index]
    last = bars[-1]
    current_dd = last.close / ath.close - 1.0

    peak = bars[0].close
    peak_date = bars[0].date
    mdd = 0.0
    mdd_peak = peak
    mdd_peak_date = peak_date
    mdd_trough = bars[0].close
    mdd_trough_date = bars[0].date
    mdd_trough_index = 0
    for index, bar in enumerate(bars):
        if bar.close >= peak:
            peak = bar.close
            peak_date = bar.date
        drawdown = bar.close / peak - 1.0
        if drawdown < mdd:
            mdd = drawdown
            mdd_peak = peak
            mdd_peak_date = peak_date
            mdd_trough = bar.close
            mdd_trough_date = bar.date
            mdd_trough_index = index

    recovered = False
    recovery_date: dt.date | None = None
    if mdd < 0:
        for bar in bars[mdd_trough_index + 1 :]:
            if bar.close >= mdd_peak:
                recovered = True
                recovery_date = bar.date
                break
    else:
        recovered = True
        recovery_date = mdd_trough_date

    return DrawdownStats(
        n=len(bars),
        first_date=bars[0].date,
        first_close=bars[0].close,
        last_date=last.date,
        last_close=last.close,
        ath=ath.close,
        ath_date=ath.date,
        current_dd=current_dd,
        mdd=mdd,
        mdd_peak_date=mdd_peak_date,
        mdd_peak=mdd_peak,
        mdd_trough_date=mdd_trough_date,
        mdd_trough=mdd_trough,
        recovered=recovered,
        recovery_date=recovery_date,
    )
