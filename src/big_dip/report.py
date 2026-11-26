"""把报告打成可读的文本表。精确数字以 JSON 为准。"""

from __future__ import annotations

from big_dip.assets import ASSET_BY_KEY, Asset, window_specs


def _display_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if ord(char) > 0x2E80 else 1
    return width


def _pad(text: str, width: int, align: str = "left") -> str:
    gap = width - _display_width(text)
    if gap <= 0:
        return text
    if align == "right":
        return " " * gap + text
    return text + " " * gap


def _price(value: float) -> str:
    absolute = abs(value)
    if absolute >= 1000:
        return f"{value:,.3f}"
    if absolute >= 1:
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _span(start: str, end: str) -> str:
    return f"{start} → {end}"


def _rows(report: dict) -> list[list[str]]:
    header = ["标的", "窗口", "样本", "收盘", "收盘高点", "当前回撤", "最大回撤", "峰 → 谷", "收复"]
    rows = [header]
    for key, asset_report in report.get("assets", {}).items():
        asset: Asset | None = ASSET_BY_KEY.get(key)
        specs = window_specs(asset) if asset is not None else ()
        labels = dict(specs)
        for kind, stats in asset_report.get("windows", {}).items():
            recovery = stats["recovery_date"] if stats["recovered"] else "未收复"
            ath = f"{_price(stats['ath'])} ({stats['ath_date']})"
            rows.append(
                [
                    asset_report["name"],
                    labels.get(kind, kind),
                    str(stats["n"]),
                    f"{_price(stats['last_close'])} ({stats['last_date']})",
                    ath,
                    _pct(stats["current_dd"]),
                    _pct(stats["mdd"]),
                    _span(stats["mdd_peak_date"], stats["mdd_trough_date"]),
                    recovery or "未收复",
                ]
            )
    return rows


def render_table(report: dict) -> str:
    lines = [
        f"截至（上海）{report.get('asof_shanghai', '')}",
        "口径：日收盘，不含红利，不含盘中高低。这不是投资建议。",
    ]
    for error in report.get("errors") or []:
        lines.append(f"失败  {error['name']} ({error['ticker']})：{error['message']}")
    rows = _rows(report)
    if len(rows) == 1:
        lines.append("没有算出任何窗口。")
        return "\n".join(lines)
    widths = [0] * len(rows[0])
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], _display_width(cell))
    aligns = ["left", "left", "right", "left", "left", "right", "right", "left", "left"]
    for row_index, row in enumerate(rows):
        pieces = [
            _pad(cell, widths[index], aligns[index]) for index, cell in enumerate(row)
        ]
        lines.append("  ".join(pieces).rstrip())
        if row_index == 0:
            lines.append("  ".join("-" * width for width in widths))
    return "\n".join(lines)
