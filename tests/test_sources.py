"""行情解析。使用本地构造的响应，不访问网络。"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from big_dip.assets import Market
from big_dip.sina_klc import SinaKlcError, decode_klc
from big_dip.drawdown import Bar
from big_dip.sources import parse_fred_csv, parse_yahoo_chart, prepend_earlier


def test_yahoo_us_bar_uses_the_new_york_session_date():
    stamp = int(dt.datetime(2026, 11, 25, 14, 30, tzinfo=dt.timezone.utc).timestamp())
    payload = json.dumps(
        {
            "chart": {
                "result": [
                    {
                        "timestamp": [stamp, stamp + 86400],
                        "indicators": {"quote": [{"close": [7182.78, None]}]},
                    }
                ]
            }
        }
    ).encode()
    bars = parse_yahoo_chart(payload, Market.US)
    assert len(bars) == 1
    assert bars[0].date == dt.date(2026, 11, 25)
    assert bars[0].close == pytest.approx(7182.78)


def test_yahoo_crypto_bar_stays_on_the_utc_date():
    stamp = int(dt.datetime(2026, 11, 25, 0, 0, tzinfo=dt.timezone.utc).timestamp())
    payload = json.dumps(
        {
            "chart": {
                "result": [
                    {
                        "timestamp": [stamp],
                        "indicators": {"quote": [{"close": [60378.2]}]},
                    }
                ]
            }
        }
    ).encode()
    bars = parse_yahoo_chart(payload, Market.CRYPTO)
    assert bars[0].date == dt.date(2026, 11, 25)


def test_yahoo_error_payload_raises():
    payload = json.dumps(
        {"chart": {"result": None, "error": {"description": "Not Found"}}}
    ).encode()
    with pytest.raises(RuntimeError, match="Not Found"):
        parse_yahoo_chart(payload, Market.US)


def test_fred_csv_skips_missing_observations():
    payload = (
        "observation_date,NASDAQCOM\n"
        "1971-02-05,100.000\n"
        "1971-02-06,.\n"
        "1971-02-08,100.840\n"
    ).encode()
    bars = parse_fred_csv(payload)
    assert [(bar.date.isoformat(), bar.close) for bar in bars] == [
        ("1971-02-05", 100.0),
        ("1971-02-08", 100.84),
    ]


def test_prepend_keeps_the_later_series_on_the_overlap_and_does_not_scale():
    early = [
        Bar(dt.date(1927, 12, 30), 17.66),
        Bar(dt.date(1950, 1, 3), 16.70),
    ]
    late = [
        Bar(dt.date(1950, 1, 3), 16.66),
        Bar(dt.date(1950, 1, 4), 16.85),
    ]
    merged, note = prepend_earlier(early, late)
    assert [(bar.date.isoformat(), bar.close) for bar in merged] == [
        ("1927-12-30", 17.66),
        ("1950-01-03", 16.66),
        ("1950-01-04", 16.85),
    ]
    assert "1950-01-03" in note
    assert "不缩放" in note


def test_sina_decoder_rejects_a_non_alphabet_payload():
    with pytest.raises(SinaKlcError):
        decode_klc("这不是行情")
