# -*- coding: utf-8 -*-
"""解析 WolfAgents 游戏日志（.log），生成供 LLM 分析的结构化数据。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_ROLE_RE = re.compile(r"^\s*-\s*(Player\d+)(?:\s*\(.*?\))?\s*:\s*(\w+)\s*$")
_GAME_ID_RE = re.compile(r"^\s*游戏ID\s*:\s*([0-9_]+)\s*$")
_EVENT_HEADER_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\]\s*(.*?)\s*\|\s*(Player\d+)\s*$")


@dataclass
class ParsedLog:
    game_id: str
    players: dict[str, str]  # PlayerX -> 身份
    raw_text: str
    events: list[dict[str, Any]]
    # 字段：thought/speech/reflection/other（心声/发言/反思/其他）
    per_player: dict[str, dict[str, list[str]]]


def _safe_read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_game_log(log_path: str | Path) -> ParsedLog:
    path = Path(log_path)
    text = _safe_read_text(path)
    lines = text.splitlines()

    game_id = ""
    players: dict[str, str] = {}

    in_players_section = False
    for line in lines[:250]:
        m = _GAME_ID_RE.match(line)
        if m:
            game_id = m.group(1)
        if "玩家列表" in line:
            in_players_section = True
            continue
        if in_players_section:
            if not line.strip():
                if players:
                    break
                continue
            m2 = _ROLE_RE.match(line)
            if m2:
                players[m2.group(1)] = m2.group(2)

    if not game_id:
        # 兜底：从文件名 game_YYYY... 中提取
        m = re.search(r"game_(\d{8}_\d{6})", path.name)
        game_id = m.group(1) if m else path.stem

    events: list[dict[str, Any]] = []
    per_player: dict[str, dict[str, list[str]]] = {
        pid: {"thought": [], "speech": [], "reflection": [], "other": []}
        for pid in players.keys()
    }

    current: dict[str, Any] | None = None
    current_lines: list[str] = []

    def flush_current() -> None:
        nonlocal current, current_lines
        if not current:
            return
        body = "\n".join(current_lines).strip()
        current["body"] = body
        events.append(current)

        pid = current.get("player")
        if pid and pid in per_player and body:
            # 启发式：抽取带标签的片段。
            # 同时保留原始文本，并尽可能拆分为 thought/speech/reflection。
            for raw in body.splitlines():
                s = raw.strip()
                if not s:
                    continue
                if s.startswith("(心声)") or s.startswith("(思考)"):
                    per_player[pid]["thought"].append(s)
                elif s.startswith("(发言)"):
                    per_player[pid]["speech"].append(s)
                elif "反思" in current.get("channel", "") or s.startswith("(印象)"):
                    per_player[pid]["reflection"].append(s)
                else:
                    per_player[pid]["other"].append(s)

        current = None
        current_lines = []

    for line in lines:
        header = _EVENT_HEADER_RE.match(line)
        if header:
            flush_current()
            ts, channel, pid = header.groups()
            current = {"time": ts, "channel": channel.strip(), "player": pid}
            current_lines = []
            continue

        if current is not None:
            if line.startswith("--------------------------------------------------------------------------------"):
                flush_current()
                continue
            # 保留缩进与普通行
            current_lines.append(line)

    flush_current()

    return ParsedLog(
        game_id=game_id,
        players=players,
        raw_text=text,
        events=events,
        per_player=per_player,
    )


def build_compact_context(parsed: ParsedLog, experience_players: dict[str, str] | None) -> dict[str, Any]:
    """
    构建用于 LLM 提示词的完整上下文负载。（无上下文压缩）
    """

    experience_players = experience_players or {}

    per_player_summary: dict[str, Any] = {}
    for pid in parsed.players.keys():
        buckets = parsed.per_player.get(pid) or {}
        per_player_summary[pid] = {
            "role": parsed.players.get(pid, "unknown"),
            "experience": experience_players.get(pid, ""),
            "thought": "\n".join(buckets.get("thought", [])),
            "speech": "\n".join(buckets.get("speech", [])),
            "reflection": "\n".join(buckets.get("reflection", [])),
            "other": "\n".join(buckets.get("other", [])),
        }

    # 提供完整的全局时间线
    timeline = [
        {
            "time": e.get("time"),
            "channel": e.get("channel"),
            "player": e.get("player"),
            "body": e.get("body", "") or "",
        }
        for e in parsed.events
    ]

    return {
        "gameId": parsed.game_id,
        "players": parsed.players,
        "timeline": timeline,
        "perPlayer": per_player_summary,
    }
