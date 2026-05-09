# -*- coding: utf-8 -*-
"""用户模式：8 个 AI + 1 名人类玩家的狼人杀流程。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
import random
from typing import Any

import numpy as np
from agentscope.agent import ReActAgent
from agentscope.message import Msg

from config import config
from core.game_engine import (
    _attach_context,
    _extract_msg_fields,
    _format_impression_context,
    moderator,
)
from core.game_logger import GameLogger
from core.knowledge_base import PlayerKnowledgeStore
from core.utils import MAX_DISCUSSION_ROUND, Players, Prompts, is_abstain_vote, majority_vote, names_to_str
from models.roles import Hunter, RoleFactory, Seer, Witch


ROLE_DISPLAY = {
    "werewolf": "狼人",
    "villager": "村民",
    "seer": "预言家",
    "witch": "女巫",
    "hunter": "猎人",
}

HIDDEN_SCOPE = "hidden"
HUMAN_ONLY_SCOPE = "human_only"


def _role_alignment(role_name: str) -> str:
    return "werewolves" if role_name == "werewolf" else "villagers"


@dataclass
class HumanPlayerRef:
    name: str


@dataclass
class HumanRoleState:
    name: str
    role_name: str
    is_alive: bool = True
    checked_players: list[str] = field(default_factory=list)
    known_identities: dict[str, str] = field(default_factory=dict)
    has_healing: bool = False
    has_poison: bool = False
    has_shot: bool = False

    def __post_init__(self) -> None:
        if self.role_name == "witch":
            self.has_healing = True
            self.has_poison = True
        if self.role_name == "hunter":
            self.has_shot = True

    def kill(self) -> None:
        self.is_alive = False


def _human_visible_options(players: Players, human: HumanRoleState) -> list[dict[str, Any]]:
    visible = []
    wolf_team = set(players.role_to_names.get("werewolf", [])) if human.role_name == "werewolf" else set()
    for name in players.name_to_role:
        if name == human.name:
            visible_role = ROLE_DISPLAY[human.role_name]
        elif name in wolf_team:
            visible_role = "狼人"
        else:
            visible_role = "未知"
        visible.append(
            {
                "name": name,
                "role": visible_role,
                "alignment": _role_alignment(players.name_to_role[name]) if name in wolf_team or name == human.name else "unknown",
                "alive": any(role.name == name for role in players.current_alive),
            }
        )
    return visible


def _emit(event_sink: Any | None, event: dict[str, Any]) -> None:
    if event_sink:
        event_sink(event)


def _emit_human_state(event_sink: Any | None, players: Players, human: HumanRoleState) -> None:
    _emit(
        event_sink,
        {
            "type": "human_role_state",
            "human": {
                "name": human.name,
                "role": human.role_name,
                "roleDisplay": ROLE_DISPLAY.get(human.role_name, human.role_name),
                "alignment": _role_alignment(human.role_name),
                "alive": human.is_alive,
                "wolfTeammates": [
                    name for name in players.role_to_names.get("werewolf", [])
                    if name != human.name
                ] if human.role_name == "werewolf" else [],
                "checkedIdentities": dict(human.known_identities),
                "abilities": {
                    "healing": human.has_healing,
                    "poison": human.has_poison,
                    "shot": human.has_shot,
                },
            },
            "players": _human_visible_options(players, human),
        },
    )


async def _observe_ai(roles: list[Any], msg: Msg, exclude_names: set[str] | None = None) -> None:
    exclude = exclude_names or set()
    for role in roles:
        if role.name in exclude:
            continue
        if hasattr(role, "observe"):
            await role.observe(msg)


def _public_msg(agent_name: str, speech: str, behavior: str = "") -> Msg:
    parts = []
    if behavior:
        parts.append(f"[表现] {behavior}")
    if speech:
        parts.append(speech)
    return Msg(agent_name, "\n".join(parts) if parts else "(无发言)", role="assistant")


def _seat_name(raw_name: str) -> str:
    if raw_name.lower().startswith("player"):
        suffix = raw_name[6:]
        if suffix.isdigit():
            return f"{int(suffix)}号"
    return raw_name


def _shuffle_candidates(
    names: list[str],
    *,
    human_name: str | None = None,
    soften_human_bias: bool = False,
) -> list[str]:
    """随机打乱候选列表，并可在需要时降低 1 号排位偏置。"""
    candidates = [name for name in names if name]
    random.shuffle(candidates)
    if soften_human_bias and human_name and human_name in candidates and len(candidates) > 1:
        candidates = [name for name in candidates if name != human_name] + [human_name]
    return candidates


def _safe_public_text(speech: str, behavior: str, fallback: str) -> str:
    """尽量只向公屏暴露可公开的最终发言。"""
    text = str(speech or fallback or "").strip()
    if not text:
        return ""
    suspicious_markers = [
        "我的发言将",
        "我应该保持",
        "我需要保持",
        "我的目的是",
        "在这一轮的讨论中",
        "我会谨慎地",
        "我会保持冷静",
        "避免过早暴露自己的身份",
    ]
    paragraphs = [seg.strip() for seg in text.split("\n\n") if seg.strip()]
    public_parts = [
        seg for seg in paragraphs
        if not any(marker in seg for marker in suspicious_markers)
    ]
    text = "\n\n".join(public_parts).strip() or paragraphs[0]
    if behavior and text.startswith(f"{behavior}"):
        text = text[len(behavior):].lstrip("：: \n")
    return text.strip()


async def _safe_call(action_name: str, func, *args, default=None, **kwargs):
    """AI 调用容错封装，避免单个模型异常导致整局直接终止。"""
    try:
        return await func(*args, **kwargs)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[user-mode] {action_name} failed: {exc}")
        return default


async def _await_human_action(
    broker: Any,
    *,
    action_type: str,
    title: str,
    prompt: str,
    options: list[dict[str, Any]] | None = None,
    input_mode: str = "text",
    placeholder: str = "",
    side: str = "center",
    stop_event: Any | None = None,
) -> dict[str, Any]:
    request = broker.create_request(
        action_type=action_type,
        title=title,
        prompt=prompt,
        options=options or [],
        inputMode=input_mode,
        placeholder=placeholder,
        side=side,
    )
    return await broker.wait_for_response(request["id"], stop_event=stop_event)


async def _human_text_turn(
    *,
    broker: Any,
    logger: GameLogger,
    category: str,
    title: str,
    prompt: str,
    placeholder: str,
    behavior: str,
    side: str,
    players: Players,
    human: HumanRoleState,
    alive_ai_roles: list[Any],
    round_public_records: list[dict[str, Any]],
    phase_label: str,
    stop_event: Any | None = None,
    scope: str = "public",
) -> None:
    reply = await _await_human_action(
        broker,
        action_type=category,
        title=title,
        prompt=prompt,
        input_mode="text",
        placeholder=placeholder,
        side=side,
        stop_event=stop_event,
    )
    speech = str(reply.get("text") or "").strip() or "我先过。"
    logger.log_message_detail(
        category,
        human.name,
        speech=speech,
        behavior=behavior,
        thought=None,
        scope=scope,
    )
    round_public_records.append(
        {
            "player": human.name,
            "speech": speech,
            "behavior": behavior,
            "phase": phase_label,
            "scope": scope,
        }
    )
    await _observe_ai(alive_ai_roles, _public_msg(human.name, speech, behavior))


def _alive_ai_roles(players: Players) -> list[Any]:
    return [role for role in players.current_alive if hasattr(role, "agent")]


def _vote_options(players: Players, *, include_abstain: bool, candidates: list[str] | None = None) -> list[dict[str, str]]:
    names = candidates or [role.name for role in players.current_alive]
    options = [{"label": _seat_name(name), "value": name} for name in names]
    if include_abstain:
        options.append({"label": "弃权", "value": "abstain"})
    return options


async def _human_vote(
    broker: Any,
    *,
    title: str,
    prompt: str,
    options: list[dict[str, str]],
    stop_event: Any | None = None,
) -> str | None:
    reply = await _await_human_action(
        broker,
        action_type="vote",
        title=title,
        prompt=prompt,
        options=options,
        input_mode="select",
        side="center",
        stop_event=stop_event,
    )
    choice = str(reply.get("choice") or "").strip()
    return None if is_abstain_vote(choice) else choice


async def werewolves_game_with_human(
    ai_agents: list[ReActAgent],
    *,
    knowledge_store: PlayerKnowledgeStore | None = None,
    player_model_map: dict[str, str] | None = None,
    game_id: str | None = None,
    event_sink: Any | None = None,
    stop_event: Any | None = None,
    human_broker: Any | None = None,
) -> tuple[str, str]:
    """用户模式主入口。"""
    assert len(ai_agents) == 8, "用户模式需要 8 个 AI 玩家。"
    if human_broker is None:
        raise RuntimeError("用户模式缺少人类交互通道")

    knowledge_store = knowledge_store or PlayerKnowledgeStore(
        checkpoint_dir=config.experience_dir,
        base_filename=config.experience_id,
    )
    knowledge_store.load()

    gid = game_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    logger = GameLogger(
        gid,
        event_sink=event_sink,
        reveal_private_thoughts=False,
        reveal_roles_in_events=False,
    )

    vote_history: list[dict[str, Any]] = []
    players = Players()
    human_name = "Player1"

    async def _check_stop() -> None:
        if stop_event is not None and getattr(stop_event, "is_set", None):
            if stop_event.is_set():
                raise asyncio.CancelledError("游戏被用户终止")
        await asyncio.sleep(0)

    async def _announce(content: str, *, observe_ai: bool = True) -> None:
        logger.log_announcement(content)
        if observe_ai:
            await _observe_ai(_alive_ai_roles(players), await moderator(content))

    async def _emit_overlay(title: str, content: str, duration_ms: int = 2000) -> None:
        _emit(
            event_sink,
            {
                "type": "overlay",
                "title": title,
                "content": content,
                "durationMs": duration_ms,
            },
        )

    roles = ["werewolf"] * 3 + ["villager"] * 3 + ["seer", "witch", "hunter"]
    np.random.shuffle(roles)
    seat_roles = {f"Player{idx + 1}": role_name for idx, role_name in enumerate(roles)}

    human = HumanRoleState(human_name, seat_roles[human_name])
    human_knowledge = knowledge_store.get_player_knowledge(human_name)
    players.add_player(HumanPlayerRef(human_name), human.role_name, human, knowledge=human_knowledge)

    for idx, agent in enumerate(ai_agents, start=2):
        role_name = seat_roles[f"Player{idx}"]
        role_obj = RoleFactory.create_role(agent, role_name)
        await agent.observe(await moderator(f"[{agent.name} ONLY] {agent.name}, your role is {role_name}."))
        instruction = role_obj.get_instruction()
        if instruction:
            await agent.observe(await moderator(f"[{agent.name} ONLY] {instruction}"))
        initial_knowledge = knowledge_store.get_player_knowledge(agent.name)
        players.add_player(agent, role_name, role_obj, knowledge=initial_knowledge)

    players_info = [(name, role_name) for name, role_name in players.name_to_role.items()]
    logger.log_players(players_info, model_map=player_model_map)
    _emit_human_state(event_sink, players, human)
    _emit(
        event_sink,
        {
            "type": "human_role_reveal",
            "role": human.role_name,
            "roleDisplay": ROLE_DISPLAY.get(human.role_name, human.role_name),
            "durationMs": 3000,
        },
    )

    game_status = "正常结束"

    try:
        await _announce("新的一局游戏开始。1号为人类玩家，其余 8 位为 AI 玩家。", observe_ai=True)
        for round_num in range(1, config.max_game_round + 1):
            await _check_stop()
            round_public_records: list[dict[str, Any]] = []
            logger.start_round(round_num)

            logger.start_night()
            await _observe_ai(_alive_ai_roles(players), await moderator(Prompts.to_all_night))
            killed_player = None
            poisoned_player = None
            night_hunter_shots: list[str] = []

            alive_wolves = [role for role in players.werewolves if role.is_alive]
            ai_wolves = [role for role in alive_wolves if role.name != human_name]
            human_is_live_wolf = human.is_alive and human.role_name == "werewolf"

            for turn_idx, werewolf in enumerate(ai_wolves, start=1):
                await _check_stop()
                context = _format_impression_context(
                    werewolf.name,
                    players,
                    vote_history,
                    round_public_records,
                    round_num,
                    "夜晚讨论",
                )
                logger.log_agent_typing(werewolf.name, "狼人讨论", scope="wolves_only")
                res = await _safe_call(
                    f"{werewolf.name} discuss_with_team",
                    werewolf.discuss_with_team,
                    _attach_context(
                        await moderator(
                            f"当前处于夜晚狼人讨论阶段（狼人讨论第{turn_idx}轮）。"
                            "请与队友快速同步思路，并尝试形成一致击杀目标。"
                            "不要仅因为 1 号是人类玩家就机械地优先针对他。"
                        ),
                        context,
                    ),
                    default=None,
                )
                if res is None:
                    continue
                speech, behavior, thought, content_raw = _extract_msg_fields(res)
                public_speech = _safe_public_text(speech, behavior, content_raw)
                logger.log_message_detail(
                    "狼人讨论",
                    werewolf.name,
                    speech=public_speech,
                    behavior=behavior,
                    thought=thought,
                    scope="wolves_only",
                )
                round_public_records.append(
                    {
                        "player": werewolf.name,
                        "speech": public_speech,
                        "behavior": behavior,
                        "phase": "狼人夜聊",
                        "scope": "wolves_only",
                    }
                )
                await _observe_ai(ai_wolves, _public_msg(werewolf.name, public_speech, behavior), exclude_names={werewolf.name})

            if human_is_live_wolf:
                await _human_text_turn(
                    broker=human_broker,
                    logger=logger,
                    category="狼人讨论",
                    title="轮到你参与狼人夜聊",
                    prompt="你是狼人。请与狼队友讨论今晚的刀口和对白天的伪装思路。",
                    placeholder="输入你对狼队友说的话",
                    behavior="压低声音与狼队友商量",
                    side="left",
                    players=players,
                    human=human,
                    alive_ai_roles=ai_wolves,
                    round_public_records=round_public_records,
                    phase_label="狼人夜聊",
                    stop_event=stop_event,
                    scope="wolves_only",
                )

            wolf_vote_candidates = _shuffle_candidates(
                [role.name for role in players.current_alive if players.name_to_role.get(role.name) != "werewolf"],
                human_name=human_name,
                soften_human_bias=(round_num == 1),
            )
            wolf_votes: list[str | None] = []
            for werewolf in ai_wolves:
                await _check_stop()
                context = _format_impression_context(
                    werewolf.name,
                    players,
                    vote_history,
                    round_public_records,
                    round_num,
                    "夜晚投票",
                )
                logger.log_agent_typing(werewolf.name, "狼人投票", scope="wolves_only")
                msg = await _safe_call(
                    f"{werewolf.name} team_vote",
                    werewolf.team_vote,
                    _attach_context(
                        await moderator(f"{Prompts.to_wolves_vote}\n补充要求：不要因为 1 号是人类玩家就固定首夜刀 1 号。"),
                        context,
                    ),
                    [players.name_to_role_obj[name] for name in wolf_vote_candidates if name in players.name_to_role_obj],
                    default=None,
                )
                if msg is None:
                    if wolf_vote_candidates:
                        wolf_votes.append(random.choice(wolf_vote_candidates))
                    continue
                speech, behavior, thought, content_raw = _extract_msg_fields(msg)
                raw_vote = (getattr(msg, "metadata", {}) or {}).get("vote")
                vote_value = str(raw_vote).strip() if raw_vote else None
                if vote_value not in wolf_vote_candidates:
                    vote_value = random.choice(wolf_vote_candidates) if wolf_vote_candidates else None
                wolf_votes.append(vote_value)
                if vote_value:
                    logger.log_vote(
                        werewolf.name,
                        vote_value,
                        "狼人投票",
                        speech=speech or content_raw,
                        behavior=behavior,
                        thought=thought,
                        scope="wolves_only",
                    )

            if human_is_live_wolf and wolf_vote_candidates:
                human_wolf_vote = await _human_vote(
                    human_broker,
                    title="请选择今晚要击杀的目标",
                    prompt="你是狼人，请在非狼人存活玩家中选择一名目标。",
                    options=_vote_options(players, include_abstain=False, candidates=wolf_vote_candidates),
                    stop_event=stop_event,
                )
                if human_wolf_vote:
                    wolf_votes.append(human_wolf_vote)
                    logger.log_vote(
                        human_name,
                        human_wolf_vote,
                        "狼人投票",
                        speech=f"我建议今晚刀 {_seat_name(human_wolf_vote)}。",
                        behavior="指向目标并作出决定",
                        thought=None,
                        scope="wolves_only",
                    )

            killed_player, wolf_votes_text, wolf_top = majority_vote(wolf_votes)
            if not killed_player and wolf_top:
                killed_player = sorted(wolf_top)[0]
                wolf_votes_text = f"{wolf_votes_text}；平票后按顺位击杀 {killed_player}"
            logger.log_vote_result(killed_player or "无人出局", wolf_votes_text, "狼人投票结果", "被选中击杀" if killed_player else "无人被击杀", scope="wolves_only")
            if human_is_live_wolf:
                await _emit_overlay("狼人投票结果", f"{wolf_votes_text}\n结果：{_seat_name(killed_player) if killed_player else '无人出局'}", 2000)

            await _check_stop()
            if human.is_alive and human.role_name == "witch":
                if human.has_healing and killed_player:
                    if killed_player == human_name:
                        rescue_reply = await _await_human_action(
                            human_broker,
                            action_type="witch_self_killed",
                            title="女巫行动：你今夜被刀",
                            prompt="今晚被刀的人是你自己。根据当前规则，女巫不能对自己使用解药，请确认后继续后续行动。",
                            options=[{"label": "我知道了", "value": "ack"}],
                            input_mode="select",
                            side="center",
                            stop_event=stop_event,
                        )
                    else:
                        rescue_reply = await _await_human_action(
                            human_broker,
                            action_type="witch_rescue",
                            title="女巫行动：是否使用解药",
                            prompt=f"今晚被刀的人是 {_seat_name(killed_player)}，你是否使用解药？",
                            options=[{"label": "使用解药", "value": "yes"}, {"label": "不使用", "value": "no"}],
                            input_mode="select",
                            side="center",
                            stop_event=stop_event,
                        )
                    logger.log_message_detail(
                        "女巫行动(解药)",
                        human_name,
                        speech="我已查看今晚的刀口并作出解药阶段决定。" if killed_player != human_name else "我已确认自己今夜被刀，且不能对自己使用解药。",
                        behavior="低头查看药瓶",
                        thought=None,
                        scope=HUMAN_ONLY_SCOPE,
                    )
                    if killed_player != human_name and rescue_reply.get("choice") == "yes":
                        human.has_healing = False
                        killed_player = None
                        logger.log_action("女巫行动", "1号女巫使用了解药", scope=HUMAN_ONLY_SCOPE)
                if human.has_poison:
                    poison_candidates = [role.name for role in players.current_alive if role.name != human_name and role.name != killed_player]
                    poison_reply = await _await_human_action(
                        human_broker,
                        action_type="witch_poison",
                        title="女巫行动：是否使用毒药",
                        prompt="请选择是否使用毒药。若不使用，选择“保留毒药”。",
                        options=[{"label": "保留毒药", "value": ""}] + _vote_options(players, include_abstain=False, candidates=poison_candidates),
                        input_mode="select",
                        side="center",
                        stop_event=stop_event,
                    )
                    logger.log_message_detail(
                        "女巫行动(毒药)",
                        human_name,
                        speech="我已作出毒药选择。",
                        behavior="轻轻握住另一瓶药",
                        thought=None,
                        scope=HUMAN_ONLY_SCOPE,
                    )
                    poison_choice = str(poison_reply.get("choice") or "").strip()
                    if poison_choice:
                        human.has_poison = False
                        poisoned_player = poison_choice
                        logger.log_action("女巫行动", f"1号女巫使用毒药毒杀了 {poisoned_player}", scope=HUMAN_ONLY_SCOPE)
                _emit_human_state(event_sink, players, human)
            else:
                for witch in players.witch:
                    if witch.name == human_name:
                        continue
                    game_state = {
                        "killed_player": killed_player,
                        "alive_players": players.current_alive,
                        "moderator": moderator,
                        "context": _format_impression_context(witch.name, players, vote_history, round_public_records, round_num, "女巫行动"),
                    }
                    logger.log_agent_typing(witch.name, "女巫行动", scope=HIDDEN_SCOPE)
                    result = await _safe_call(f"{witch.name} witch_action", witch.night_action, game_state, default={}) or {}
                    logger.log_message_detail(
                        "女巫行动(解药)",
                        witch.name,
                        speech=result.get("resurrect_speech"),
                        behavior=result.get("resurrect_behavior"),
                        thought=result.get("resurrect_thought"),
                        scope=HIDDEN_SCOPE,
                    )
                    logger.log_message_detail(
                        "女巫行动(毒药)",
                        witch.name,
                        speech=result.get("poison_speech"),
                        behavior=result.get("poison_behavior"),
                        thought=result.get("poison_thought"),
                        scope=HIDDEN_SCOPE,
                    )
                    if result.get("resurrect"):
                        killed_player = None
                        logger.log_action("女巫行动", f"使用解药救了 {result.get('resurrect')}", scope=HIDDEN_SCOPE)
                    if result.get("poison"):
                        poisoned_player = result.get("poison")
                        logger.log_action("女巫行动", f"使用毒药毒杀了 {poisoned_player}", scope=HIDDEN_SCOPE)

            await _check_stop()
            if human.is_alive and human.role_name == "seer":
                checked_options = [
                    name for name in players.name_to_role
                    if name != human_name and name not in human.checked_players and any(role.name == name for role in players.current_alive)
                ]
                checked_options = _shuffle_candidates(checked_options, human_name=human_name, soften_human_bias=(round_num == 1))
                if checked_options:
                    reply = await _await_human_action(
                        human_broker,
                        action_type="seer_check",
                        title="预言家行动",
                        prompt="请选择一名尚未查验过的存活玩家。",
                        options=_vote_options(players, include_abstain=False, candidates=checked_options),
                        input_mode="select",
                        side="center",
                        stop_event=stop_event,
                    )
                    target = str(reply.get("choice") or "").strip()
                    if target:
                        human.checked_players.append(target)
                        result = "狼人" if players.name_to_role.get(target) == "werewolf" else "好人"
                        human.known_identities[target] = result
                        logger.log_message_detail(
                            "预言家行动",
                            human_name,
                            speech=f"我查验了 {_seat_name(target)}。",
                            behavior="闭眼感知命运的回响",
                            thought=None,
                            scope=HUMAN_ONLY_SCOPE,
                        )
                        logger.log_action("预言家查验", f"查验 {target}, 结果: {result}", scope=HUMAN_ONLY_SCOPE)
                        _emit_human_state(event_sink, players, human)
                        await _emit_overlay("查验结果", f"{_seat_name(target)} 是{result}", 2200)
            else:
                for seer in players.seer:
                    if seer.name == human_name:
                        continue
                    game_state = {
                        "alive_players": players.current_alive,
                        "moderator": moderator,
                        "name_to_role": players.name_to_role,
                        "context": _format_impression_context(seer.name, players, vote_history, round_public_records, round_num, "预言家行动"),
                        "human_name": human_name,
                        "soften_human_bias": round_num == 1,
                    }
                    logger.log_agent_typing(seer.name, "预言家行动", scope=HIDDEN_SCOPE)
                    result = await _safe_call(f"{seer.name} seer_action", seer.night_action, game_state, default={}) or {}
                    logger.log_message_detail(
                        "预言家行动",
                        seer.name,
                        speech=result.get("speech"),
                        behavior=result.get("behavior"),
                        thought=result.get("thought"),
                        scope=HIDDEN_SCOPE,
                    )
                    if result and result.get("action") == "check":
                        logger.log_action("预言家查验", f"查验 {result.get('target')}, 结果: {result.get('result')}", scope=HIDDEN_SCOPE)

            logger.start_day()
            dead_tonight: list[str] = []
            for item in [killed_player, poisoned_player]:
                if item and item not in dead_tonight:
                    dead_tonight.append(item)

            if human.role_name == "hunter" and human.is_alive and killed_player == human_name and poisoned_player != human_name and human.has_shot:
                shot_reply = await _await_human_action(
                    human_broker,
                    action_type="hunter_shoot",
                    title="猎人开枪",
                    prompt="你将出局，可以选择带走一名仍存活的玩家，也可以放弃。",
                    options=[{"label": "不开枪", "value": ""}] + _vote_options(players, include_abstain=False, candidates=[role.name for role in players.current_alive if role.name != human_name and role.name not in dead_tonight]),
                    input_mode="select",
                    side="center",
                    stop_event=stop_event,
                )
                shot_target = str(shot_reply.get("choice") or "").strip()
                logger.log_message_detail(
                    "猎人开枪",
                    human_name,
                    speech="我已作出是否开枪的决定。",
                    behavior="举起枪口",
                    thought=None,
                    scope=HUMAN_ONLY_SCOPE,
                )
                if shot_target:
                    human.has_shot = False
                    night_hunter_shots.append(shot_target)
                    logger.log_action("猎人开枪", f"猎人 {human_name} 开枪击杀了 {shot_target}", scope=HUMAN_ONLY_SCOPE)
            else:
                for hunter in players.hunter:
                    if hunter.name == human_name:
                        continue
                    if killed_player == hunter.name and poisoned_player != hunter.name:
                        alive_for_hunter = [p for p in players.current_alive if p.name not in dead_tonight]
                        if alive_for_hunter:
                            context = _format_impression_context(hunter.name, players, vote_history, round_public_records, round_num, "猎人开枪")
                            logger.log_agent_typing(hunter.name, "猎人开枪", scope=HIDDEN_SCOPE)
                            shoot_res = await hunter.shoot(alive_for_hunter, moderator, context)
                            if shoot_res:
                                logger.log_message_detail(
                                    "猎人开枪",
                                    hunter.name,
                                    speech=shoot_res.get("speech"),
                                    behavior=shoot_res.get("behavior"),
                                    thought=shoot_res.get("thought"),
                                    scope=HIDDEN_SCOPE,
                                )
                                target = shoot_res.get("target") if shoot_res.get("shoot") else None
                                if target:
                                    night_hunter_shots.append(target)
                                    logger.log_action("猎人开枪", f"猎人 {hunter.name} 开枪击杀了 {target}", scope=HIDDEN_SCOPE)

            for target in night_hunter_shots:
                if target and target not in dead_tonight:
                    dead_tonight.append(target)

            logger.log_death("夜晚死亡", dead_tonight)
            players.update_players(dead_tonight)
            _emit_human_state(event_sink, players, human)

            if dead_tonight:
                await _announce(f"天亮了。昨晚被淘汰的玩家有：{names_to_str(dead_tonight)}。")
                await _emit_overlay("夜晚结果", f"昨晚出局：{', '.join(_seat_name(name) for name in dead_tonight)}", 2200)
            else:
                await _announce("天亮了。昨晚是平安夜，无人出局。")
                await _emit_overlay("夜晚结果", "昨晚平安夜，无人出局。", 2200)

            if round_num == 1 and dead_tonight:
                for dead_name in dead_tonight:
                    if dead_name == human_name:
                        await _human_text_turn(
                            broker=human_broker,
                            logger=logger,
                            category="遗言",
                            title="请发表遗言",
                            prompt="你已出局，请输入遗言。",
                            placeholder="输入你的遗言",
                            behavior="平静地留下最后的话语",
                            side="center",
                            players=players,
                            human=human,
                            alive_ai_roles=_alive_ai_roles(players),
                            round_public_records=round_public_records,
                            phase_label="遗言",
                            stop_event=stop_event,
                        )
                    elif dead_name in players.name_to_role_obj:
                        role_obj = players.name_to_role_obj[dead_name]
                        context = _format_impression_context(dead_name, players, vote_history, round_public_records, round_num, "遗言")
                        logger.log_agent_typing(dead_name, "遗言")
                        last_msg = await _safe_call(
                            f"{dead_name} leave_last_words",
                            role_obj.leave_last_words,
                            _attach_context(await moderator(Prompts.to_dead_player.format(dead_name)), context),
                            default=None,
                        )
                        if last_msg is None:
                            continue
                        speech, behavior, thought, content_raw = _extract_msg_fields(last_msg)
                        public_speech = _safe_public_text(speech, behavior, content_raw)
                        logger.log_message_detail("遗言", dead_name, speech=public_speech, behavior=behavior, thought=thought)
                        await _observe_ai(_alive_ai_roles(players), _public_msg(dead_name, public_speech, behavior), exclude_names={dead_name})
                        round_public_records.append({"player": dead_name, "speech": public_speech, "behavior": behavior, "phase": "遗言"})

            result = players.check_winning()
            if result:
                logger.log_announcement(f"游戏结束: {result}")
                await _emit_overlay("游戏结束", result, 4000)
                break

            await _announce(
                "现在进入白天讨论阶段，请按 1 号到 9 号的顺序依次发言。"
            )
            for role in list(players.current_alive):
                await _check_stop()
                if role.name == human_name:
                    await _human_text_turn(
                        broker=human_broker,
                        logger=logger,
                        category="白天讨论",
                        title="轮到你发言",
                        prompt="请在弹窗中输入你的发言，与其他玩家讨论。",
                        placeholder="输入你的发言",
                        behavior="看向众人并开始发言",
                        side="center",
                        players=players,
                        human=human,
                        alive_ai_roles=[r for r in _alive_ai_roles(players) if r.name != human_name],
                        round_public_records=round_public_records,
                        phase_label="白天讨论",
                        stop_event=stop_event,
                    )
                    continue

                context = _format_impression_context(role.name, players, vote_history, round_public_records, round_num, "白天讨论")
                logger.log_agent_typing(role.name, "白天讨论")
                msg = await _safe_call(
                    f"{role.name} day_discussion",
                    role.day_discussion,
                    _attach_context(await moderator(""), context),
                    default=None,
                )
                if msg is None:
                    fallback_speech = "我先继续观察，暂时没有更多补充。"
                    logger.log_message_detail("白天讨论", role.name, speech=fallback_speech, behavior="短暂沉默后谨慎发言", thought=None)
                    round_public_records.append({"player": role.name, "speech": fallback_speech, "behavior": "短暂沉默后谨慎发言", "phase": "白天讨论"})
                    await _observe_ai(_alive_ai_roles(players), _public_msg(role.name, fallback_speech, "短暂沉默后谨慎发言"), exclude_names={role.name})
                    continue
                speech, behavior, thought, content_raw = _extract_msg_fields(msg)
                public_speech = _safe_public_text(speech, behavior, content_raw)
                logger.log_message_detail("白天讨论", role.name, speech=public_speech, behavior=behavior, thought=thought)
                round_public_records.append({"player": role.name, "speech": public_speech, "behavior": behavior, "phase": "白天讨论"})
                await _observe_ai(_alive_ai_roles(players), _public_msg(role.name, public_speech, behavior), exclude_names={role.name})

            await _announce("现在进入放逐投票阶段，请所有存活玩家投票。")
            round_vote_records: list[dict[str, Any]] = []
            day_votes: list[str | None] = []

            for role in list(players.current_alive):
                await _check_stop()
                if role.name == human_name:
                    vote_value = await _human_vote(
                        human_broker,
                        title="请选择你要放逐的玩家",
                        prompt="你可以选择一名存活玩家投票，或选择弃权。",
                        options=_vote_options(players, include_abstain=True, candidates=[r.name for r in players.current_alive if r.name != human_name]),
                        stop_event=stop_event,
                    )
                    day_votes.append(vote_value)
                    logger.log_message_detail(
                        "投票",
                        human_name,
                        speech=f"我投给了 {_seat_name(vote_value)}。" if vote_value else "我选择弃权。",
                        behavior="做出自己的投票选择",
                        thought=None,
                        action=f"投票给 {vote_value}" if vote_value else "弃票",
                    )
                    round_vote_records.append({"round": round_num, "phase": "白天投票", "voter": human_name, "target": vote_value})
                    continue

                context = _format_impression_context(role.name, players, vote_history, round_public_records, round_num, "白天投票")
                logger.log_agent_typing(role.name, "投票")
                shuffled_alive = _shuffle_candidates(
                    [r.name for r in players.current_alive if r.name != role.name],
                    human_name=human_name,
                    soften_human_bias=(round_num == 1),
                )
                vote_alive_players = [players.name_to_role_obj[name] for name in shuffled_alive if name in players.name_to_role_obj]
                msg = await _safe_call(
                    f"{role.name} vote",
                    role.vote,
                    _attach_context(await moderator(Prompts.to_all_vote.format(names_to_str(players.current_alive))), context),
                    vote_alive_players,
                    default=None,
                )
                if msg is None:
                    vote_value = random.choice(shuffled_alive) if shuffled_alive else None
                    day_votes.append(vote_value)
                    logger.log_message_detail("投票", role.name, speech="我投出了这一票。", behavior="在短暂思考后给出选择", thought=None, action=f"投票给 {vote_value}" if vote_value else "弃票")
                    round_vote_records.append({"round": round_num, "phase": "白天投票", "voter": role.name, "target": vote_value})
                    continue
                speech, behavior, thought, content_raw = _extract_msg_fields(msg)
                raw_vote = (getattr(msg, "metadata", {}) or {}).get("vote")
                vote_value = None if is_abstain_vote(raw_vote) else str(raw_vote).strip()
                valid_targets = {r.name for r in players.current_alive if r.name != role.name}
                if vote_value not in valid_targets:
                    vote_value = random.choice(list(valid_targets)) if valid_targets else None
                day_votes.append(vote_value)
                public_speech = _safe_public_text(speech, behavior, content_raw)
                if vote_value:
                    logger.log_vote(role.name, vote_value, "投票", speech=public_speech, behavior=behavior, thought=thought)
                else:
                    logger.log_message_detail("投票", role.name, speech=public_speech, behavior=behavior, thought=thought, action="弃票")
                round_vote_records.append({"round": round_num, "phase": "白天投票", "voter": role.name, "target": vote_value})

            voted_player, votes_text, top_candidates = majority_vote(day_votes)
            if not voted_player and top_candidates:
                voted_player = sorted(top_candidates)[0]
                votes_text = f"{votes_text}；平票后按顺位放逐 {voted_player}"
            logger.log_vote_result(voted_player or "无人出局", votes_text, "投票结果", "被投出" if voted_player else "无人被投出")
            vote_history.extend(round_vote_records)
            await _emit_overlay("放逐投票结果", f"{votes_text}\n结果：{_seat_name(voted_player) if voted_player else '无人出局'}", 2000)

            if voted_player == human_name:
                await _human_text_turn(
                    broker=human_broker,
                    logger=logger,
                    category="遗言",
                    title="请发表遗言",
                    prompt="你被放逐出局，请输入遗言。",
                    placeholder="输入你的遗言",
                    behavior="望向众人留下最后的判断",
                    side="center",
                    players=players,
                    human=human,
                    alive_ai_roles=[r for r in _alive_ai_roles(players) if r.name != human_name],
                    round_public_records=round_public_records,
                    phase_label="遗言",
                    stop_event=stop_event,
                )

            shot_player = None
            if voted_player == human_name and human.role_name == "hunter" and human.has_shot and poisoned_player != human_name:
                shot_reply = await _await_human_action(
                    human_broker,
                    action_type="hunter_shoot",
                    title="猎人开枪",
                    prompt="你被放逐出局，可以选择带走一名仍存活的玩家，也可以放弃。",
                    options=[{"label": "不开枪", "value": ""}] + _vote_options(players, include_abstain=False, candidates=[r.name for r in players.current_alive if r.name != human_name]),
                    input_mode="select",
                    side="center",
                    stop_event=stop_event,
                )
                shot_player = str(shot_reply.get("choice") or "").strip() or None
                logger.log_message_detail("猎人开枪", human_name, speech="我已决定是否开枪。", behavior="握紧猎枪作出选择", thought=None)
                if shot_player:
                    human.has_shot = False
                    logger.log_action("猎人开枪", f"猎人 {human_name} 开枪击杀了 {shot_player}")
            else:
                for hunter in players.hunter:
                    if hunter.name == human_name:
                        continue
                    if voted_player == hunter.name:
                        context = _format_impression_context(hunter.name, players, vote_history, round_public_records, round_num, "猎人开枪")
                        logger.log_agent_typing(hunter.name, "猎人开枪")
                        shoot_res = await _safe_call(
                            f"{hunter.name} day_hunter_shoot",
                            hunter.shoot,
                            players.current_alive,
                            moderator,
                            context,
                            default=None,
                        )
                        if shoot_res:
                            logger.log_message_detail("猎人开枪", hunter.name, speech=shoot_res.get("speech"), behavior=shoot_res.get("behavior"), thought=shoot_res.get("thought"))
                            shot_player = shoot_res.get("target") if shoot_res.get("shoot") else None
                            if shot_player:
                                logger.log_action("猎人开枪", f"猎人 {hunter.name} 开枪击杀了 {shot_player}")

            dead_today = [name for name in [voted_player, shot_player] if name]
            logger.log_death("白天死亡", dead_today)
            players.update_players(dead_today)
            _emit_human_state(event_sink, players, human)

            result = players.check_winning()
            if result:
                logger.log_announcement(f"游戏结束: {result}")
                await _emit_overlay("游戏结束", result, 4000)
                break

        knowledge_store.bulk_update(players.export_all_knowledge())
        knowledge_store.save()
        return str(logger.log_file), str(knowledge_store.path)
    except BaseException:
        game_status = "异常终止"
        raise
    finally:
        try:
            human_broker.clear_pending()
        except Exception:
            pass
        logger.close(status=game_status)
