# -*- coding: utf-8 -*-
"""将 analysisData 注入到 report_demo.html 模板中。"""

from __future__ import annotations

import json
from pathlib import Path


def _find_analysis_data_region(html: str) -> tuple[int, int]:
    """返回现有 `const analysisData = ...;` 区域的 (start_idx, end_idx)。"""

    anchor = "const analysisData ="
    start = html.find(anchor)
    if start == -1:
        raise ValueError("Template missing 'const analysisData =' anchor")

    brace_start = html.find("{", start)
    if brace_start == -1:
        raise ValueError("Template missing '{' after analysisData anchor")

    # 大括号匹配（忽略字符串内的括号）
    depth = 0
    i = brace_start
    in_str = False
    str_char = ""
    escape = False
    while i < len(html):
        ch = html[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == str_char:
                in_str = False
        else:
            if ch in ('"', "'"):
                in_str = True
                str_char = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    # 在闭合大括号后继续寻找分号
                    semi = html.find(";", i)
                    if semi == -1:
                        raise ValueError(
                            "Template missing ';' after analysisData object")
                    return start, semi + 1
        i += 1

    raise ValueError("Failed to match braces for analysisData")


def inject_analysis_data(template_path: str | Path, analysis_data: dict) -> str:
    template_path = Path(template_path)
    html = template_path.read_text(encoding="utf-8", errors="replace")

    start, end = _find_analysis_data_region(html)

    payload = json.dumps(analysis_data, ensure_ascii=False, indent=4)
    replacement = f"const analysisData = {payload};"

    return html[:start] + replacement + html[end:]


def write_report(template_path: str | Path, analysis_data: dict, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    rendered = inject_analysis_data(template_path, analysis_data)
    output_path.write_text(rendered, encoding="utf-8")
    return output_path
