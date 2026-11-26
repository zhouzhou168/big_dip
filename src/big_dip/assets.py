"""标的、交易日截止规则和窗口名称。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")


class Market(str, Enum):
    CN = "cn"
    US = "us"
    CRYPTO = "crypto"


@dataclass(frozen=True)
class Asset:
    key: str
    name: str
    ticker: str
    unit: str
    market: Market
    source: str
    symbol: str
    start: dt.date | None
    extra_windows: tuple[tuple[str, str], ...] = ()
    note: str = ""


# extra_windows 与 WINDOW_LABELS 一样，是 (窗口代号, 中文名)。
# modern1957 的固定起点写在 drawdown.slice_window 里。

ASSETS: tuple[Asset, ...] = (
    Asset(
        key="sse",
        name="上证指数",
        ticker="000001.SH",
        unit="点",
        market=Market.CN,
        source="sina",
        symbol="sh000001",
        start=dt.date(1990, 12, 19),
        note="官方回溯起点 1990-12-19（指数发布日是 1991-07-15，日线可以回溯到基日）。",
    ),
    Asset(
        key="szse",
        name="深证成指",
        ticker="399001.SZ",
        unit="点",
        market=Market.CN,
        source="sina",
        symbol="sz399001",
        start=dt.date(1994, 7, 20),
        note="只保留官方基期 1994-07-20 及之后的收盘。新浪日线里更早的点位是非官方前史，不参与计算。",
    ),
    Asset(
        key="cyb",
        name="创业板指",
        ticker="399006.SZ",
        unit="点",
        market=Market.CN,
        source="sina",
        symbol="sz399006",
        start=dt.date(2010, 6, 1),
        note="官方起点 2010-06-01。",
    ),
    Asset(
        key="ixic",
        name="纳斯达克综合指数",
        ticker="^IXIC",
        unit="点",
        market=Market.US,
        source="nasdaq",
        symbol="^IXIC",
        start=dt.date(1971, 2, 5),
        note=(
            "纳斯达克综合指数，不是纳斯达克 100。目标起点是 1971-02-05 的基点 100。"
            "官网历史若从更晚开始，更早的交易日用 FRED NASDAQCOM 补上。"
        ),
    ),
    Asset(
        key="gspc",
        name="标普500",
        ticker="^GSPC",
        unit="点",
        market=Market.US,
        source="tencent+yahoo",
        symbol="^GSPC",
        start=None,
        extra_windows=(("modern1957", "1957-03-04起"),),
        note=(
            "1950 年起用腾讯 us.INX 日收盘，更早的年份用 Yahoo ^GSPC 补到 1927 年末的历史综合指数。"
            "modern1957 从 1957-03-04 单独计算，不替换主序列。"
        ),
    ),
    Asset(
        key="btc",
        name="BTC",
        ticker="BTC-USD",
        unit="美元",
        market=Market.CRYPTO,
        source="coinmetrics",
        symbol="btc",
        start=dt.date(2010, 7, 18),
        note="CoinMetrics PriceUSD 的最早可靠成交日价。这不是 2009 年创世块的价格。",
    ),
    Asset(
        key="eth",
        name="ETH",
        ticker="ETH-USD",
        unit="美元",
        market=Market.CRYPTO,
        source="coinmetrics",
        symbol="eth",
        start=dt.date(2015, 8, 8),
        note="CoinMetrics PriceUSD。起点是主网上线后的最早可靠日价，约 2015-08-08。",
    ),
)

ASSET_BY_KEY = {asset.key: asset for asset in ASSETS}

WINDOW_LABELS = (
    ("inception", "成立以来"),
    ("ytd", "今年以来"),
    ("y1", "近1年"),
    ("y5", "近5年"),
)


def window_specs(asset: Asset) -> tuple[tuple[str, str], ...]:
    return WINDOW_LABELS + asset.extra_windows


def last_allowed_date(market: Market, now: dt.datetime) -> dt.date:
    """最新一根已经收完的日线日期。

    A 股看上海时间 15:00，美股看美东 16:00 的常规收盘。
    BTC/ETH 只用已经结束的 UTC 日，当天未走完的 K 线不计入。
    美股半天市（提前到 13:00 收盘）在 16:00 之前运行时，当天可能先被跳过。
    """
    if now.tzinfo is None:
        raise ValueError("now 需要带时区")
    if market is Market.CN:
        local = now.astimezone(SHANGHAI)
        if local.time() >= dt.time(15, 0):
            return local.date()
        return local.date() - dt.timedelta(days=1)
    if market is Market.US:
        local = now.astimezone(NEW_YORK)
        if local.time() >= dt.time(16, 0):
            return local.date()
        return local.date() - dt.timedelta(days=1)
    utc = now.astimezone(dt.timezone.utc)
    return utc.date() - dt.timedelta(days=1)
