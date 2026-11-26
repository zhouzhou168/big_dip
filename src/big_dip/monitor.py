"""把各标的的日收盘收成一份回撤报告。"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

from big_dip.assets import (
    ASSETS,
    SHANGHAI,
    Asset,
    last_allowed_date,
    window_specs,
)
from big_dip.drawdown import Bar, analyze, slice_window
from big_dip.sources import fetch_coinmetrics, fetch_sina, load_nasdaq, load_sp500

METHODOLOGY = {
    "price": "只用日收盘价",
    "dividends": "不含红利再投资",
    "intraday": "不使用盘中最高价或最低价",
    "drawdown": "时点 t 的回撤 = 当日收盘 / 截至当日的滚动最高收盘 - 1。历史最大回撤是窗口内最负的那个值。",
    "peak": "最大回撤的峰值，是谷值之前滚动最高收盘最后一次被打平或刷新的那一天。",
    "ath": "收盘高点是窗口内最高收盘第一次出现的日期。",
    "recovery": "收复日是谷值之后，收盘第一次大于或等于该次峰值的日期；没有这一天就是尚未收复。",
    "windows": "今年以来、近 1 年、近 5 年只在窗口内计算，不把窗口开始前的高点带进来。近 1 年和近 5 年以最新收盘日向前推日历年。",
    "sessions": "A 股用上海时间 15:00 之后的最新交易日；美股用美东 16:00 之后的最新交易日；BTC 和 ETH 只用已经结束的 UTC 日。",
}


def prepare_bars(bars: list[Bar], asset: Asset, now: dt.datetime) -> list[Bar]:
    """丢掉非正数、官方起点之前，以及尚未收完的交易日。同一天保留最后一笔。"""
    limit = last_allowed_date(asset.market, now)
    chosen: dict[dt.date, Bar] = {}
    for bar in bars:
        if not math.isfinite(bar.close) or bar.close <= 0:
            continue
        if asset.start is not None and bar.date < asset.start:
            continue
        if bar.date > limit:
            continue
        chosen[bar.date] = bar
    return [chosen[day] for day in sorted(chosen)]


def _load(asset: Asset, now: dt.datetime, cache_dir: Path | None) -> tuple[list[Bar], dict]:
    limit = last_allowed_date(asset.market, now)
    if asset.source == "sina":
        bars, meta = fetch_sina(asset.symbol, cache_dir=cache_dir)
    elif asset.key == "gspc":
        bars, meta = load_sp500(limit, cache_dir=cache_dir)
    elif asset.key == "ixic":
        bars, meta = load_nasdaq(limit, cache_dir=cache_dir)
    elif asset.source == "coinmetrics":
        begin = (asset.start or dt.date(2010, 1, 1)) - dt.timedelta(days=10)
        bars, meta = fetch_coinmetrics(asset.symbol, begin, limit, cache_dir=cache_dir)
    else:
        raise RuntimeError(f"未知数据源 {asset.source}")
    meta = dict(meta)
    meta["sessions_through"] = limit.isoformat()
    meta["rows_downloaded"] = len(bars)
    return bars, meta


def build_report(
    now: dt.datetime | None = None,
    only: list[str] | None = None,
    cache_dir: Path | None = None,
    progress=None,
) -> dict:
    """抓取并计算。单个标的失败时记在 errors 里，其余标的继续。"""
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    selected = list(ASSETS)
    if only is not None:
        wanted = set(only)
        selected = [asset for asset in ASSETS if asset.key in wanted]

    shanghai = now.astimezone(SHANGHAI)
    report: dict = {
        "asof_shanghai": shanghai.isoformat(timespec="seconds"),
        "timezone": "Asia/Shanghai",
        "methodology": METHODOLOGY,
        "assets": {},
    }
    errors: list[dict] = []
    for asset in selected:
        if progress is not None:
            progress(asset)
        try:
            raw, meta = _load(asset, now, cache_dir)
            bars = prepare_bars(raw, asset, now)
            meta["rows_used"] = len(bars)
            if len(bars) < 2:
                raise RuntimeError(
                    f"截止 {meta['sessions_through']} 的有效收盘不足 2 根（得到 {len(bars)} 根）"
                )
            anchor = bars[-1].date
            windows = {}
            for kind, _label in window_specs(asset):
                sliced = slice_window(bars, kind, anchor)
                if len(sliced) < 2:
                    raise RuntimeError(f"窗口 {kind} 里有效收盘不足 2 根")
                windows[kind] = analyze(sliced).as_dict()
            report["assets"][asset.key] = {
                "name": asset.name,
                "ticker": asset.ticker,
                "unit": asset.unit,
                "note": asset.note,
                "source": meta,
                "windows": windows,
            }
        except Exception as exc:
            errors.append(
                {
                    "key": asset.key,
                    "name": asset.name,
                    "ticker": asset.ticker,
                    "message": f"{type(exc).__name__}: {exc}",
                }
            )
    if errors:
        report["errors"] = errors
    return report
