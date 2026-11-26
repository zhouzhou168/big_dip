"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from big_dip.assets import ASSET_BY_KEY, ASSETS
from big_dip.monitor import build_report
from big_dip.report import render_table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="用日收盘计算七个标的的当前回撤和历史最大回撤。"
    )
    parser.add_argument(
        "-o",
        "--output",
        default="out/drawdown.json",
        help="JSON 输出路径，默认 out/drawdown.json",
    )
    parser.add_argument(
        "--assets",
        help="只计算这些 key，逗号分隔。默认全部：" + ",".join(asset.key for asset in ASSETS),
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        help="把原始下载缓存在这个目录，便于短时间重跑。不要把缓存提交进 git。",
    )
    args = parser.parse_args(argv)
    only = None
    if args.assets:
        only = [item.strip() for item in args.assets.split(",") if item.strip()]
        unknown = [key for key in only if key not in ASSET_BY_KEY]
        if unknown:
            known = ", ".join(asset.key for asset in ASSETS)
            print(f"未知标的：{', '.join(unknown)}。可选：{known}", file=sys.stderr)
            return 2
    report = build_report(
        only=only,
        cache_dir=args.cache_dir,
        progress=lambda asset: print(
            f"正在抓取 {asset.name} ({asset.ticker}) …",
            file=sys.stderr,
        ),
    )
    print(render_table(report))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nJSON 已写入 {destination}")
    return 1 if report.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
