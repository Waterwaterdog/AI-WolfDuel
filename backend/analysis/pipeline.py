# -*- coding: utf-8 -*-
"""三智能体分析流水线：生成 analysisData 并渲染最终报告 HTML。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import config

from analysis.log_parser import parse_game_log, build_compact_context
from analysis.report_template import write_report
from analysis.schemas import AnalysisData


def _load_experience(experience_path: str | Path) -> dict[str, Any]:
    path = Path(experience_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}


def _player_display_name(player_id: str) -> str:
    # Player6 -> 玩家6
    suffix = "".join(ch for ch in player_id if ch.isdigit())
    return f"玩家{suffix}" if suffix else player_id


def _merge_analysis_data(
    game_id: str,
    roles: dict[str, str],
    psychology_out: dict[str, Any],
    network_out: dict[str, Any],
) -> dict[str, Any]:
    player_ids = sorted(roles.keys(), key=lambda x: int(
        "".join(c for c in x if c.isdigit()) or 0))

    players = []
    for pid in player_ids:
        role = roles.get(pid, "villager")
        is_wolf = role == "werewolf"
        players.append(
            {
                "id": pid,
                "name": _player_display_name(pid),
                "role": role,
                "isWerewolf": is_wolf,
            }
        )

    analysis_texts = {
        "stats": psychology_out["analysisTexts"]["stats"],
        "players": psychology_out["analysisTexts"].get("players", {}),
        "network": network_out["analysisTexts"]["network"],
    }

    merged = {
        "gameId": game_id,
        "players": players,
        "analysisTexts": analysis_texts,
        "psychology": psychology_out["psychology"],
        "network": network_out["network"],
    }

    # 使用模板对应的 schema 校验，避免前端可视化因字段缺失而崩溃
    AnalysisData.model_validate(merged)
    return merged


async def run_analysis(
    *,
    log_path: str | Path,
    experience_path: str | Path | None = None,
    template_path: str | Path | None = None,
    output_path: str | Path | None = None,
) -> Path:
    """运行三智能体分析流水线，并写出最终报告 HTML。"""

    log_path = Path(log_path)
    if template_path is None:
        template_path = Path(config.root_dir) / "data" / \
            "analysis_reports" / "report_demo.html"
    else:
        template_path = Path(template_path)

    parsed = parse_game_log(log_path)

    if experience_path is None:
        # 默认：按 game_id 在 static/ 下自动匹配经验文件
        cand = Path(config.root_dir) / "static" / \
            f"players_experience_{parsed.game_id}.json"
        experience_path = cand
    exp = _load_experience(experience_path)
    exp_players = (exp.get("players") or {}) if isinstance(exp, dict) else {}

    context = build_compact_context(parsed, exp_players)
    roles = parsed.players
    player_ids = sorted(roles.keys(), key=lambda x: int(
        "".join(c for c in x if c.isdigit()) or 0))

    is_valid, err = config.validate()
    if not is_valid:
        raise RuntimeError(f"LLM 配置校验失败，无法进行分析：{err}")

    from analysis.agents import (
        create_analysis_agent,
        ask_for_schema,
        PSYCHOLOGY_SYS,
        NETWORK_SYS,
        PsychologyAgentOutputStrict,
        NetworkAgentOutputStrict,
        build_psychology_prompt,
        build_network_prompt,
    )

    psychology_agent = create_analysis_agent(
        "PsychologyAgent", PSYCHOLOGY_SYS)
    network_agent = create_analysis_agent("NetworkAgent", NETWORK_SYS)

    psy_prompt = build_psychology_prompt(context, player_ids)
    net_prompt = build_network_prompt(context, player_ids)

    psy_out_model = await ask_for_schema(psychology_agent, psy_prompt, PsychologyAgentOutputStrict)
    net_out_model = await ask_for_schema(network_agent, net_prompt, NetworkAgentOutputStrict)

    analysis_data = _merge_analysis_data(
        parsed.game_id,
        roles,
        psy_out_model.model_dump(),
        net_out_model.model_dump(),
    )

    if output_path is None:
        output_path = Path(config.root_dir) / "data" / \
            "analysis_reports" / f"report_{parsed.game_id}.html"
    else:
        output_path = Path(output_path)

    return write_report(template_path, analysis_data, output_path)
