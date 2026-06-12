# -*- coding: utf-8 -*-
"""CLI 入口：python -m analysis --log ..."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _ensure_backend_on_syspath() -> None:
    # 当以脚本文件方式执行（run_path）时，sys.path 可能不包含 backend/。
    backend_dir = Path(__file__).resolve().parent.parent
    backend_str = str(backend_dir)
    if backend_str not in sys.path:
        sys.path.insert(0, backend_str)


_ensure_backend_on_syspath()

from analysis.pipeline import run_analysis  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate WolfAgents analysis report HTML")
    p.add_argument("--log", required=True, help="Path to game_*.log")
    p.add_argument("--experience", default=None,
                   help="Path to players_experience_*.json")
    p.add_argument("--template", default=None, help="Path to report_demo.html")
    p.add_argument("--out", default=None, help="Output HTML path")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    out = asyncio.run(
        run_analysis(
            log_path=Path(args.log),
            experience_path=Path(args.experience) if args.experience else None,
            template_path=Path(args.template) if args.template else None,
            output_path=Path(args.out) if args.out else None,
        )
    )
    print(str(out))


if __name__ == "__main__":
    main()
