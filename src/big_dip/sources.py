"""抓取日收盘。只保留收盘价，不读盘中高低，也不做复权。"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import ssl
import time
import urllib.error
import urllib.parse
from pathlib import Path

from big_dip.assets import Market, NEW_YORK
from big_dip.drawdown import Bar
from big_dip.http_util import http_get
from big_dip.sina_klc import decode_klc

YAHOO_CHARTS = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
    "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
)
SINA_KLC = "https://finance.sina.com.cn/realstock/company/{symbol}/hisdata/klc_kl.js"
COINMETRICS = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
TENCENT_KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
NASDAQ_HISTORY = "https://api.nasdaq.com/api/quote/COMP/historical"
FRED_GRAPH = "https://fred.stlouisfed.org/graph/fredgraph.csv"
# 1950 年之前的标普日线。Yahoo 图表接口被限流时，用这份公开转存补腾讯起点之前的日期。
# 文件可能停更，不能拿来代替 1950 年之后的行情。
SPX_EARLY_MIRROR = "https://datayyy.com/data_csv/sp500daily.csv"


def fetch_sina(symbol: str, *, cache_dir: Path | None = None) -> tuple[list[Bar], dict]:
    # 查询参数只用来避开旧缓存，日期本身不参与计算。
    url = f"{SINA_KLC.format(symbol=symbol)}?d={int(time.time())}"
    payload = http_get(url, referer="https://finance.sina.com.cn/", cache_dir=cache_dir)
    text = payload.decode("utf-8", "replace")
    if "=" not in text:
        raise RuntimeError("新浪日 K 响应里没有行情字符串")
    encoded = text.split("=", 1)[1].split(";", 1)[0].strip().strip('"').strip("'")
    rows = decode_klc(encoded)
    bars = [Bar(row["date"], float(row["close"])) for row in rows]
    meta = {
        "provider": "新浪财经",
        "url": SINA_KLC.format(symbol=symbol),
        "symbol": symbol,
        "field": "close",
        "adjusted": False,
        "detail": "klc_kl.js 日 K，取不复权收盘价。",
    }
    return bars, meta


def _add_years(day: dt.date, years: int) -> dt.date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def _unix(day: dt.date) -> int:
    moment = dt.datetime.combine(day, dt.time(0), tzinfo=dt.timezone.utc)
    return int(moment.timestamp())


def parse_yahoo_chart(payload: bytes, market: Market) -> list[Bar]:
    document = json.loads(payload)
    chart = document.get("chart") or {}
    result = chart.get("result")
    if not result:
        error = chart.get("error") or {}
        description = error.get("description") if isinstance(error, dict) else None
        raise RuntimeError(description or "Yahoo 没有返回日线")
    block = result[0]
    timestamps = block.get("timestamp") or []
    closes = (block.get("indicators") or {}).get("quote", [{}])[0].get("close") or []
    zone = NEW_YORK if market is Market.US else dt.timezone.utc
    bars: list[Bar] = []
    for stamp, close in zip(timestamps, closes):
        if stamp is None or close is None:
            continue
        session = dt.datetime.fromtimestamp(int(stamp), zone).date()
        bars.append(Bar(session, float(close)))
    return bars


def _yahoo_chunk(
    symbol: str,
    start: dt.date,
    end: dt.date,
    market: Market,
    cache_dir: Path | None,
) -> list[Bar]:
    """一个年份段。query1 被限流时换 query2，各试一次。"""
    quoted = urllib.parse.quote(symbol, safe="")
    query = f"?period1={_unix(start)}&period2={_unix(end + dt.timedelta(days=3))}&interval=1d"
    last_error: Exception | None = None
    for template in YAHOO_CHARTS:
        url = template.format(symbol=quoted) + query
        try:
            payload = http_get(url, cache_dir=cache_dir, timeout=90, retries=1)
            return parse_yahoo_chart(payload, market)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429:
                raise
    assert last_error is not None
    raise last_error


def fetch_yahoo(
    symbol: str,
    start: dt.date,
    end: dt.date,
    market: Market,
    *,
    cache_dir: Path | None = None,
) -> tuple[list[Bar], dict]:
    """按大约 5 年一段下载日线 close。整段历史一次请求经常被回 429。"""
    if end < start:
        raise RuntimeError("Yahoo 请求的结束日早于开始日")
    bars: list[Bar] = []
    cursor = start
    chunks = 0
    while cursor <= end:
        chunk_end = min(end, _add_years(cursor, 5))
        try:
            bars.extend(_yahoo_chunk(symbol, cursor, chunk_end, market, cache_dir))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Yahoo {symbol} 在 {cursor.isoformat()} 起的这一段没有下载成功：{exc}"
            ) from exc
        chunks += 1
        if chunk_end >= end:
            break
        cursor = chunk_end - dt.timedelta(days=7)
        time.sleep(0.8)
    meta = {
        "provider": "Yahoo Finance",
        "url": "https://query1.finance.yahoo.com/v8/finance/chart/",
        "symbol": symbol,
        "field": "close",
        "adjusted": False,
        "chunks": chunks,
        "detail": "chart API 的日线 close，约 5 年一段拼接。不用复权收盘，不含红利。",
    }
    return bars, meta


def prepend_earlier(early: list[Bar], late: list[Bar]) -> tuple[list[Bar], str]:
    """把早段里、后段起点之前的收盘接在前面。重叠的日期以后段为准，不缩放价格。"""
    if not late:
        return list(early), ""
    if not early:
        return list(late), ""
    cutoff = late[0].date
    head = [bar for bar in early if bar.date < cutoff]
    early_prices = {bar.date: bar.close for bar in early}
    late_prices = {bar.date: bar.close for bar in late}
    shared = sorted(set(early_prices) & set(late_prices))
    note = ""
    if shared:
        day = shared[-1]
        gap = early_prices[day] / late_prices[day] - 1.0
        note = (
            f"衔接日 {day.isoformat()}：早段收盘 {early_prices[day]:.6g}，"
            f"后段收盘 {late_prices[day]:.6g}，相对差 {gap:.4%}。"
            "只补后段起点之前的交易日，两边价格都不缩放。"
        )
    merged: dict[dt.date, Bar] = {bar.date: bar for bar in head}
    for bar in late:
        merged[bar.date] = bar
    return [merged[day] for day in sorted(merged)], note


def _parse_mdy(text: str) -> dt.date:
    month, day, year = text.split("/")
    return dt.date(int(year), int(month), int(day))


def _parse_price(text: str) -> float:
    return float(str(text).replace(",", ""))


def _fred_context() -> ssl.SSLContext:
    # 圣路易斯联储的 CSV 在 TLS 1.3 下会一直不返回正文，固定到 TLS 1.2。
    context = ssl.create_default_context()
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    return context


def parse_fred_csv(payload: bytes) -> list[Bar]:
    text = payload.decode("utf-8-sig")
    bars: list[Bar] = []
    for row in csv.DictReader(io.StringIO(text)):
        raw_date = row.get("observation_date") or ""
        raw_value = ""
        for key, value in row.items():
            if key != "observation_date":
                raw_value = value or ""
                break
        if not raw_date or raw_value in {"", "."}:
            continue
        price = float(raw_value)
        if price <= 0:
            continue
        bars.append(Bar(dt.date.fromisoformat(raw_date), price))
    return bars


def fetch_fred_series(
    series_id: str,
    start: dt.date,
    end: dt.date,
    *,
    cache_dir: Path | None = None,
) -> list[Bar]:
    """按大约 10 年一段下载 FRED 日线。缺失日在 CSV 里是小数点，直接跳过。"""
    collected: dict[dt.date, float] = {}
    cursor = start
    while cursor <= end:
        chunk_end = min(end, _add_years(cursor, 10))
        url = (
            f"{FRED_GRAPH}?id={urllib.parse.quote(series_id)}"
            f"&cosd={cursor.isoformat()}&coed={chunk_end.isoformat()}"
        )
        payload = http_get(
            url,
            cache_dir=cache_dir,
            timeout=40,
            retries=3,
            user_agent="curl/8.5.0",
            ssl_context=_fred_context(),
        )
        for bar in parse_fred_csv(payload):
            if start <= bar.date <= end:
                collected[bar.date] = bar.close
        cursor = chunk_end + dt.timedelta(days=1)
    return [Bar(day, collected[day]) for day in sorted(collected)]


def parse_close_csv(payload: bytes) -> list[Bar]:
    """读取带 Date、Close 两列的日线 CSV。"""
    text = payload.decode("utf-8-sig")
    bars: list[Bar] = []
    for row in csv.DictReader(io.StringIO(text)):
        raw_date = (row.get("Date") or row.get("date") or "").strip()
        raw_close = (row.get("Close") or row.get("close") or "").strip()
        if not raw_date or not raw_close:
            continue
        price = float(raw_close)
        if price <= 0:
            continue
        bars.append(Bar(dt.date.fromisoformat(raw_date), price))
    return bars


def fetch_tencent_daily(
    symbol: str,
    start: dt.date,
    end: dt.date,
    *,
    cache_dir: Path | None = None,
) -> list[Bar]:
    """腾讯日 K，每次最多约 2000 根，从结束日往前翻。取第 3 列收盘。"""
    collected: dict[dt.date, float] = {}
    cursor_end = end
    for _ in range(40):
        if cursor_end < start:
            break
        url = (
            f"{TENCENT_KLINE}?param={symbol},day,"
            f"{start.isoformat()},{cursor_end.isoformat()},2000,"
        )
        payload = http_get(url, cache_dir=cache_dir, timeout=60)
        document = json.loads(payload)
        inner = (document.get("data") or {}).get(symbol) or {}
        if not isinstance(inner, dict):
            break
        rows = inner.get("day") or inner.get("qfqday") or []
        if not rows:
            break
        for row in rows:
            session = dt.date.fromisoformat(row[0])
            if start <= session <= end:
                collected[session] = float(row[2])
        first = min(dt.date.fromisoformat(row[0]) for row in rows)
        if len(rows) < 2000 or first <= start:
            break
        cursor_end = first - dt.timedelta(days=1)
        time.sleep(0.1)
    return [Bar(day, collected[day]) for day in sorted(collected)]


def fetch_nasdaq_comp(
    start: dt.date,
    end: dt.date,
    *,
    cache_dir: Path | None = None,
) -> list[Bar]:
    """Nasdaq 官网 COMP 日线历史。行是从新到旧，这里按日期排好。"""
    collected: dict[dt.date, float] = {}
    cursor = start
    while cursor <= end:
        chunk_end = min(end, _add_years(cursor, 5))
        url = (
            f"{NASDAQ_HISTORY}?assetclass=index"
            f"&fromdate={cursor.isoformat()}&todate={chunk_end.isoformat()}&limit=10000"
        )
        payload = http_get(
            url,
            cache_dir=cache_dir,
            timeout=60,
            referer="https://www.nasdaq.com/",
        )
        document = json.loads(payload)
        data = document.get("data") or {}
        table = data.get("tradesTable") or {}
        rows = table.get("rows") or []
        total = data.get("totalRecords")
        if isinstance(total, int) and total > len(rows):
            raise RuntimeError(
                f"Nasdaq 历史在 {cursor.isoformat()} 到 {chunk_end.isoformat()} 被截断"
                f"（返回 {len(rows)} / {total}）"
            )
        for row in rows:
            raw_close = row.get("close")
            if not raw_close or raw_close in {"--", "N/A"}:
                continue
            try:
                session = _parse_mdy(row["date"])
                price = _parse_price(raw_close)
            except (KeyError, ValueError):
                continue
            if price > 0:
                collected[session] = price
        cursor = chunk_end + dt.timedelta(days=1)
        time.sleep(0.1)
    return [Bar(day, collected[day]) for day in sorted(collected)]


def _prefix_reaches(early: list[Bar], late_first: dt.date) -> bool:
    return bool(early) and early[-1].date >= late_first - dt.timedelta(days=10)


def _keep_prefix(early: list[Bar], late_first: dt.date) -> list[Bar]:
    ordered = sorted(early, key=lambda bar: bar.date)
    if _prefix_reaches(ordered, late_first):
        return ordered
    return []


def _sp500_prefix(
    late_first: dt.date, cache_dir: Path | None
) -> tuple[list[Bar], str, str]:
    """1950 年之前的标普收盘。先试 Yahoo，被限流就改用公开转存。"""
    errors: list[str] = []
    try:
        early, _meta = fetch_yahoo(
            "^GSPC",
            dt.date(1927, 12, 1),
            late_first + dt.timedelta(days=7),
            Market.US,
            cache_dir=cache_dir,
        )
    except Exception as exc:
        errors.append(f"Yahoo {type(exc).__name__}: {exc}")
        early = []
    kept = _keep_prefix(early, late_first)
    if kept:
        return kept, "yahoo", ""
    if early:
        errors.append("Yahoo 早段没有接到腾讯序列的起点")
    try:
        payload = http_get(SPX_EARLY_MIRROR, cache_dir=cache_dir, timeout=60, retries=2)
        mirrored = [
            bar
            for bar in parse_close_csv(payload)
            if bar.date <= late_first + dt.timedelta(days=7)
        ]
    except Exception as exc:
        errors.append(f"转存 {type(exc).__name__}: {exc}")
        return [], "", "；".join(errors)
    kept = _keep_prefix(mirrored, late_first)
    if kept:
        return kept, "mirror", ""
    errors.append("转存文件没有接到 1950 年附近")
    return [], "", "；".join(errors)


def _nasdaq_prefix(
    late_first: dt.date, cache_dir: Path | None
) -> tuple[list[Bar], str, str]:
    """1971-02-05 到官网历史起点之前。先用 FRED NASDAQCOM，不行再试 Yahoo。"""
    if late_first <= dt.date(1971, 2, 5):
        return [], "", ""
    errors: list[str] = []
    try:
        early = fetch_fred_series(
            "NASDAQCOM",
            dt.date(1971, 2, 5),
            late_first + dt.timedelta(days=5),
            cache_dir=cache_dir,
        )
    except Exception as exc:
        errors.append(f"FRED {type(exc).__name__}: {exc}")
        early = []
    kept = _keep_prefix(early, late_first)
    if kept:
        return kept, "fred", ""
    try:
        early, _meta = fetch_yahoo(
            "^IXIC",
            dt.date(1971, 2, 1),
            late_first + dt.timedelta(days=7),
            Market.US,
            cache_dir=cache_dir,
        )
    except Exception as exc:
        errors.append(f"Yahoo {type(exc).__name__}: {exc}")
        early = []
    kept = _keep_prefix(early, late_first)
    if kept:
        return kept, "yahoo", ""
    if not errors:
        errors.append("早段没有接到官网历史的起点")
    return [], "", "；".join(errors)


def load_sp500(end: dt.date, *, cache_dir: Path | None = None) -> tuple[list[Bar], dict]:
    """1950 年起用腾讯 us.INX；更早的年份优先用 Yahoo，否则用公开转存补上。"""
    late = fetch_tencent_daily("us.INX", dt.date(1940, 1, 1), end, cache_dir=cache_dir)
    if len(late) < 2:
        raise RuntimeError("腾讯 us.INX 没有返回可用的标普日线")
    early, prefix_name, prefix_error = _sp500_prefix(late[0].date, cache_dir)
    bars, splice = prepend_earlier(early, late)
    if prefix_name == "yahoo":
        detail = (
            "1950 年起是腾讯证券 us.INX 的日收盘；1950 年之前用 Yahoo Finance ^GSPC 的日收盘补齐，"
            "这样主序列可以覆盖 1927 年末开始的历史综合指数。两边都是不复权收盘，不含红利。"
        )
        provider = "腾讯证券 + Yahoo Finance"
    elif prefix_name == "mirror":
        detail = (
            "1950 年起是腾讯证券 us.INX 的日收盘。Yahoo 图表接口这次没有返回 1950 年之前的日线，"
            f"早段改用公开转存 {SPX_EARLY_MIRROR}（一份停更的 Yahoo ^GSPC 收盘）。"
            "只取腾讯起点之前的日期，衔接日不缩放。"
        )
        provider = "腾讯证券 + Yahoo 历史转存"
    else:
        detail = (
            "本次只用腾讯证券 us.INX 的日收盘。这条序列目前从 1950 年附近开始，"
            "没有包含 1929–1932 年的回撤。"
            + (f" 早段没有用上：{prefix_error}" if prefix_error else "")
        )
        provider = "腾讯证券"
    meta = {
        "provider": provider,
        "symbols": ["us.INX", "^GSPC"],
        "field": "close",
        "adjusted": False,
        "detail": detail,
        "splice": splice or None,
        "early_prefix": prefix_name or None,
    }
    return bars, meta


def load_nasdaq(end: dt.date, *, cache_dir: Path | None = None) -> tuple[list[Bar], dict]:
    """Nasdaq 官网 COMP 为主；更早的年份用 FRED NASDAQCOM 补到 1971-02-05。"""
    late = fetch_nasdaq_comp(dt.date(1971, 2, 5), end, cache_dir=cache_dir)
    if len(late) < 2:
        raise RuntimeError("Nasdaq 官网没有返回可用的综合指数日线")
    early, prefix_name, prefix_error = _nasdaq_prefix(late[0].date, cache_dir)
    bars, splice = prepend_earlier(early, late)
    if prefix_name == "fred":
        detail = (
            "Nasdaq 官网 COMP 历史日收盘为主（不是纳斯达克 100）。"
            "官网序列开始之前，用圣路易斯联储 FRED 的 NASDAQCOM 补到 1971-02-05。"
            "两边都是不复权收盘，不含红利。FRED 的 CSV 用 TLS 1.2 下载。"
        )
        provider = "Nasdaq + FRED"
    elif prefix_name == "yahoo":
        detail = (
            "Nasdaq 官网 COMP 历史日收盘为主（不是纳斯达克 100）。"
            "FRED 没有补上早段，改用 Yahoo Finance ^IXIC 补到 1971-02-05。"
        )
        provider = "Nasdaq + Yahoo Finance"
    else:
        detail = (
            "本次只用 Nasdaq 官网 COMP 的日收盘。这条历史没有覆盖到 1971-02-05，"
            "实际从数据源的第一天起算；2000–2002 年的回撤仍在样本里。"
            + (f" 早段没有用上：{prefix_error}" if prefix_error else "")
        )
        provider = "Nasdaq"
    meta = {
        "provider": provider,
        "symbols": ["COMP", "^IXIC"],
        "field": "close",
        "adjusted": False,
        "detail": detail,
        "splice": splice or None,
        "early_prefix": prefix_name or None,
        "history_url": NASDAQ_HISTORY,
    }
    return bars, meta


def fetch_coinmetrics(
    asset: str,
    start: dt.date,
    end: dt.date,
    *,
    cache_dir: Path | None = None,
) -> tuple[list[Bar], dict]:
    """CoinMetrics community PriceUSD，1 日频率。分页直到没有 next_page_url。"""
    start_time = start.isoformat()
    end_time = (end + dt.timedelta(days=1)).isoformat()
    url: str | None = (
        f"{COINMETRICS}?assets={urllib.parse.quote(asset)}"
        f"&metrics=PriceUSD&frequency=1d&start_time={start_time}"
        f"&end_time={end_time}&page_size=10000&paging_from=start"
    )
    bars: list[Bar] = []
    pages = 0
    while url:
        pages += 1
        if pages > 20:
            raise RuntimeError("CoinMetrics 分页异常，已停止")
        document = json.loads(http_get(url, cache_dir=cache_dir, timeout=90))
        for row in document.get("data") or []:
            raw_price = row.get("PriceUSD")
            if raw_price in (None, ""):
                continue
            session = dt.date.fromisoformat(str(row["time"])[:10])
            bars.append(Bar(session, float(raw_price)))
        url = document.get("next_page_url") or None
    meta = {
        "provider": "CoinMetrics Community API",
        "url": COINMETRICS,
        "symbol": asset,
        "metric": "PriceUSD",
        "frequency": "1d",
        "field": "PriceUSD",
        "adjusted": False,
        "pages": pages,
        "detail": (
            "日频 PriceUSD，时间戳按 UTC 日。交易所日收盘和这个参考价可能差零点几个百分点；"
            "整段历史只用这一套价格，不把另一家的最近收盘接在后面。"
        ),
    }
    return bars, meta
