"""解码新浪财经指数日 K（klc_kl.js）的 1479 号比特流。

页面上的行情脚本把日线压成一段类 Base64 字符串。这里只实现当前指数历史
使用的 1479 变体，读出每个交易日的日期和收盘价。成交量也会按同样的比特
布局读完，否则后面的价格会对不齐，但调用方可以忽略它。
"""

from __future__ import annotations

import datetime as dt

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_ALPHA = {ch: i for i, ch in enumerate(_ALPHABET)}
# 低 4 位到“哪些价格字段再加 1 比特”的对照，顺序与新浪脚本一致。
_FIELD_BONUS = (0, 3, 5, 6, 9, 10, 12, 15, 17, 18, 20, 23, 24, 27, 29, 30)
_EPOCH_DAYS = 7657  # 1970-01-01 起算，第 0 日是 1990-12-19


class SinaKlcError(ValueError):
    """日 K 比特流无法按 1479 变体解码。"""


def _js_mod(n: int, m: int) -> int:
    """与 JavaScript 的 `%` 一致：商向 0 截断，余数符号跟随被除数。"""
    remainder = n % m
    if n < 0 and remainder:
        return remainder - m
    return remainder


class _Reader:
    def __init__(self, payload: str) -> None:
        data: list[int] = []
        for ch in payload:
            if ch not in _ALPHA:
                raise SinaKlcError(f"日 K 字符串含无法识别的字符 {ch!r}")
            data.append(_ALPHA[ch])
        self._data = data
        self._n = len(data)
        self._i = 0
        self._bit = 0
        self._state: dict = {}

    def _bit1(self) -> bool:
        if self._i >= self._n:
            return False
        bit = self._data[self._i] & (1 << self._bit)
        self._bit += 1
        if self._bit >= 6:
            self._bit = 0
            self._i += 1
        return bool(bit)

    def _need(self) -> int:
        if self._i >= self._n:
            raise SinaKlcError("日 K 数据在字段中间结束")
        return self._data[self._i]

    def _read_fields(
        self,
        widths: list[int],
        signed: list[int] | None = None,
        keep_pair: list[int] | None = None,
    ) -> list:
        signed = signed or []
        keep_pair = keep_pair or []
        out: list = []
        for index, width in enumerate(widths):
            if not width:
                out.append(0)
                continue
            if self._i >= self._n:
                return out
            sign = signed[index] if index < len(signed) else 0
            if width <= 30:
                value = self._read_unsigned(width)
                if sign and value >= (1 << (width - 1)):
                    value -= 1 << width
                out.append(value)
            else:
                pair = self._read_fields([30, width - 30], [0, sign])
                if index < len(keep_pair) and keep_pair[index]:
                    out.append(pair)
                else:
                    out.append(pair[0] + pair[1] * (1 << 30))
        return out

    def _read_unsigned(self, width: int) -> int:
        remaining = width
        value = 0
        while remaining > 0:
            take = 6 - self._bit
            if remaining < take:
                take = remaining
            chunk = (self._need() >> self._bit) & ((1 << take) - 1)
            value |= chunk << (width - remaining)
            self._bit += take
            if self._bit >= 6:
                self._bit -= 6
                self._i += 1
            remaining -= take
        return value

    def _advance_days(self, steps: int) -> dt.date:
        for _ in range(steps):
            self._state["d"] += 1
            weekday = _js_mod(self._state["d"], 7)
            if weekday in (3, 4):
                self._state["d"] += 5 - weekday
        ordinal = dt.date(1970, 1, 1).toordinal() + _EPOCH_DAYS + self._state["d"]
        return dt.date.fromordinal(ordinal)

    def decode(self) -> list[dict]:
        header = self._read_fields([12, 6])
        if len(header) < 2:
            raise SinaKlcError("日 K 文件头不完整")
        version, flags = header
        series_flags = 63 ^ flags
        if version != 1479:
            raise SinaKlcError(
                f"不支持的新浪日 K 版本 {version}（当前只解码 1479）"
            )
        if series_flags >= 1:
            return []
        return self._decode_1479()

    def _decode_1479(self) -> list[dict]:
        state = self._state
        state["lv"] = 0
        state["ld"] = 0
        state["cd"] = 0
        state["cv"] = [0, 0]
        state["p"] = self._read_fields([6])[0]
        state["d"] = self._read_fields([18], [1])[0] - 1
        scale = 10 ** state["p"]
        md, mv = self._read_fields([3, 3])
        state["md"] = md
        state["mv"] = mv
        bars: list[dict] = []
        low30 = (1 << 30) - 1  # ~(3 << 30) 的低 30 位
        bit30 = 1 << 30
        while True:
            header = self._read_fields([6])
            if not header:
                break
            control = header[0]
            info: dict = {"c": control, "d": 1}
            if control & 32:
                self._read_extension(info)
            session = self._advance_days(info["d"])
            for key in ("v", "d"):
                if "x_" + key in info:
                    state["l" + key] = info["x_" + key]
                if "u_" + key not in info:
                    info["u_" + key] = state["l" + key]
            lengths = [info["u_d"]] * 4 + [info["u_v"]]
            bonus = _FIELD_BONUS[control & 15]
            if info["u_v"] & 1:
                bonus = 31 - bonus
            if control & 16:
                lengths[4] += 2
            for field in range(5):
                if bonus & (1 << (4 - field)):
                    lengths[field] += 1
                lengths[field] *= 3
            delta = self._read_fields(lengths, [1, 0, 0, 1, 1], [0, 0, 0, 0, 1])
            base = state["cd"] + delta[0]
            volume = delta[4]
            if isinstance(volume, int):
                volume = [volume, 0 if volume >= 0 else -1]
            state["cd"] = base + delta[3]
            low = state["cv"][0] + volume[0]
            carry = 1 if ((state["cv"][0] & low30) + (volume[0] & low30)) & bit30 else 0
            state["cv"] = [low & low30, state["cv"][1] + volume[1] + carry]
            bars.append(
                {
                    "date": session,
                    "open": base / scale,
                    "high": (base + delta[1]) / scale,
                    "low": (base - delta[2]) / scale,
                    "close": (base + delta[3]) / scale,
                    "volume": (state["cv"][0] & (bit30 - 1)) + state["cv"][1] * bit30,
                }
            )
        if not bars:
            raise SinaKlcError("日 K 解码后没有任何交易日")
        return bars

    def _read_extension(self, info: dict) -> None:
        state = self._state
        while True:
            code = self._read_fields([6])[0]
            if (16 | code) == 63:
                slot = "x" if code & 16 else "u"
                parts = self._read_fields([3, 3])
                info[slot + "_d"] = parts[0] + state["md"]
                info[slot + "_v"] = parts[1] + state["mv"]
                return
            if code & 32:
                field = "d" if code & 8 else "v"
                slot = "x" if code & 16 else "u"
                info[slot + "_" + field] = (code & 7) + state["m" + field]
                return
            day_code = code & 15
            if day_code == 0:
                info["d"] = self._read_fields([6])[0]
            elif day_code == 1:
                state["d"] = self._read_fields([18])[0]
                info["d"] = 0
            else:
                info["d"] = day_code
            if not (code & 16):
                return


def decode_klc(payload: str) -> list[dict]:
    """把 klc_kl.js 引号内的字符串解成按日期排序的日线。"""
    text = payload.strip()
    if text.endswith(";"):
        text = text[:-1]
    if "=" in text and not text.startswith(("H", "A", "B", "C", "D", "E", "F", "G", "I", "J")):
        text = text.split("=", 1)[1]
    text = text.strip().strip('"').strip("'")
    return _Reader(text).decode()
