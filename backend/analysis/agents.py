# -*- coding: utf-8 -*-
"""基于 AgentScope 的三节点分析智能体。"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar, Literal

from pydantic import BaseModel, ValidationError

from config import config

from agentscope.agent import ReActAgent
from agentscope.formatter import (
    DashScopeMultiAgentFormatter,
    OpenAIMultiAgentFormatter,
    OllamaMultiAgentFormatter,
)
from agentscope.model import DashScopeChatModel, OpenAIChatModel, OllamaChatModel
from agentscope.message import Msg

from analysis.schemas import (
    Psychology,
    Network,
    StatsAnalysisTexts,
    NetworkAnalysisTexts,
    PlayerAnalysisText,
)


T = TypeVar("T", bound=BaseModel)


_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 常见 OpenAI 风格 key
    (re.compile(r"\bsk-[A-Za-z0-9]{10,}\b"), "sk-***"),
    # 常见 Bearer token
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{10,}\b"), "Bearer ***"),
]


def _redact_secrets(text: str) -> str:
    redacted = text
    for pattern, repl in _SECRET_PATTERNS:
        redacted = pattern.sub(repl, redacted)
    return redacted


def _excerpt(text: str, *, max_len: int = 1200) -> str:
    if not text:
        return ""
    safe = _redact_secrets(text)
    if len(safe) <= max_len:
        return safe
    return safe[:max_len] + "\n...(已截断)..."


def _sanitize_json_text(text: str) -> str:
    """尽量修复“接近 JSON 但不严格”的文本。

    目前主要针对：JSON 字符串字面量中出现未转义的换行符（\n/\r/Unicode line separators），
    这会导致整体 JSON 解析失败，而我们又可能退化抽取到内部子对象。
    """

    if not text:
        return text

    out: list[str] = []
    in_str = False
    escape = False

    for ch in text:
        if in_str:
            if escape:
                escape = False
                out.append(ch)
                continue

            if ch == "\\":
                escape = True
                out.append(ch)
                continue

            if ch == '"':
                in_str = False
                out.append(ch)
                continue

            # 将字符串中的原始换行符修复为 \n
            if ch in ("\r", "\n", "\u2028", "\u2029"):
                out.append("\\n")
                continue

            out.append(ch)
            continue

        # 非字符串上下文
        if ch == '"':
            in_str = True
            out.append(ch)
            continue
        out.append(ch)

    return "".join(out)


def _normalize_model_output(obj: Any) -> str:
    """将 AgentScope 的各种返回类型统一归一为单一字符串。

    不同 formatter/model 组合下，AgentScope 的返回可能是：
    - Msg（含 .content）
    - str
    - list（元素可能是 Msg/dict/str）
    - dict（可能包含 'content'）
    """

    if obj is None:
        return ""

    if isinstance(obj, str):
        return obj

    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")

    # Msg 类对象
    if hasattr(obj, "content"):
        try:
            return _normalize_model_output(getattr(obj, "content"))
        except Exception:
            return str(obj)

    if isinstance(obj, dict):
        # 兼容 OpenAI/兼容接口可能返回的结构化内容块：
        # {"type": "thinking", "thinking": "..."} / {"type": "text", "text": "..."}
        # 这类块往往不是严格 JSON（text 内可能包含未转义的换行），因此这里直接抽取文本字段。
        if "type" in obj:
            block_type = str(obj.get("type") or "").lower()
            if block_type in {"text", "output_text"} and "text" in obj:
                return _normalize_model_output(obj.get("text"))
            if block_type in {"thinking", "reasoning"}:
                # 忽略思考/推理块，避免污染 JSON 抽取
                return ""

        if "content" in obj:
            return _normalize_model_output(obj.get("content"))
        return json.dumps(obj, ensure_ascii=False, indent=2)

    if isinstance(obj, list):
        parts: list[str] = []
        for item in obj:
            s = _normalize_model_output(item)
            if s:
                parts.append(s)
        return "\n".join(parts)

    return str(obj)


def _extract_json(text: str) -> str:
    if not text:
        raise ValueError("empty model output")

    def _extract_first_json_object(payload: str) -> str:
        decoder = json.JSONDecoder()

        # 扫描 JSON 对象/数组的起始位置。
        for idx in range(len(payload)):
            ch = payload[idx]
            if ch not in "{[":
                continue
            try:
                _, end = decoder.raw_decode(payload[idx:])
                return payload[idx: idx + end].strip()
            except json.JSONDecodeError:
                # 尝试对该段文本做一次轻量修复（常见是字符串内未转义换行）
                try:
                    repaired = _sanitize_json_text(payload[idx:])
                    _, end = decoder.raw_decode(repaired)
                    return repaired[:end].strip()
                except json.JSONDecodeError:
                    continue

        raise ValueError("no json object found")

    def _extract_prefer_outermost(payload: str) -> str:
        """优先尝试从首个 '{'/'[' 开始解析“外层对象”，失败再退化为扫描。"""

        decoder = json.JSONDecoder()
        start_idx = -1
        for i, ch in enumerate(payload):
            if ch in "{[":
                start_idx = i
                break
        if start_idx == -1:
            raise ValueError("no json object found")

        candidate = payload[start_idx:]
        try:
            _, end = decoder.raw_decode(candidate)
            return candidate[:end].strip()
        except json.JSONDecodeError:
            repaired = _sanitize_json_text(candidate)
            try:
                _, end = decoder.raw_decode(repaired)
                return repaired[:end].strip()
            except json.JSONDecodeError:
                return _extract_first_json_object(payload)

    # 优先处理 ```json``` 围栏内容；即便包含额外文本，也只解析第一个 JSON 对象。
    # 有些模型会缺失结尾 ```，这里做降级容错：取 ```json 之后的内容进行解析。
    m = re.search(r"```json\s*(.*?)\s*```", text, flags=re.DOTALL)
    if m:
        fenced = m.group(1).strip()
        return _extract_prefer_outermost(fenced)

    fence_start = text.find("```json")
    if fence_start != -1:
        after = text.find("\n", fence_start)
        if after == -1:
            after = fence_start + len("```json")
        else:
            after += 1
        fence_end = text.find("```", after)
        fenced = text[after:] if fence_end == -1 else text[after:fence_end]
        fenced = fenced.strip()
        if fenced:
            return _extract_prefer_outermost(fenced)

    return _extract_first_json_object(text)


def _build_model_and_formatter() -> tuple[Any, Any]:
    if config.model_provider == "dashscope":
        return (
            DashScopeChatModel(api_key=config.dashscope_api_key,
                               model_name=config.dashscope_model_name),
            DashScopeMultiAgentFormatter(),
        )

    if config.model_provider == "openai":
        # 使用可用的 OpenAI 兼容配置进行分析。
        # 优先使用分析模块独立配置（ANALYSIS_OPENAI_*），否则回退 Player1/全局配置。
        cfg = config.openai_analysis_config or config.openai_player_configs[0]
        return (
            OpenAIChatModel(
                api_key=cfg.get("api_key"),
                model_name=cfg.get("model_name"),
                client_args={"base_url": cfg.get("base_url")},
            ),
            OpenAIMultiAgentFormatter(),
        )

    if config.model_provider == "ollama":
        return (
            OllamaChatModel(model_name=config.ollama_model_name),
            OllamaMultiAgentFormatter(),
        )

    raise ValueError(f"不支持的模型提供商: {config.model_provider}")


def create_analysis_agent(name: str, sys_prompt: str) -> ReActAgent:
    model, formatter = _build_model_and_formatter()
    return ReActAgent(
        name=name,
        sys_prompt=sys_prompt,
        model=model,
        formatter=formatter,
        print_hint_msg=False,
    )


async def ask_for_schema(agent: ReActAgent, user_prompt: str, schema: type[T], *, max_retries: int = 3) -> T:
    """要求智能体输出可被指定 Pydantic schema 解析的 JSON。"""

    last_err: str | None = None
    last_raw: str | None = None
    last_missing: list[str] = []
    for attempt in range(max_retries + 1):
        suffix = ""
        if attempt and last_err:
            required_top_keys = list(
                getattr(schema, "model_fields", {}).keys())
            suffix = (
                "\n\n上一次输出无法通过校验。请严格只输出一个 JSON 对象（不要解释、不要 markdown）。\n"
                "请将上一条输出修复为符合要求的 JSON。\n"
            )
            if last_raw:
                suffix += "\n上一条输出如下（已截断/脱敏）：\n" + _excerpt(last_raw) + "\n"
            if required_top_keys:
                suffix += "\n必须包含的顶层字段：" + ", ".join(required_top_keys) + "\n"
            if last_missing:
                suffix += "缺失字段（按校验结果）：" + ", ".join(last_missing) + "\n"
            suffix += f"\n校验/解析错误: {last_err}\n"

        msg = Msg("User", user_prompt + suffix, role="user")
        try:
            resp = await agent(msg)
            raw = _normalize_model_output(resp)
        except Exception as exc:
            last_err = f"agent 调用异常: {exc}"
            last_raw = None
            continue

        last_raw = raw

        try:
            json_text = _extract_json(raw)
            data = json.loads(json_text)

            # 容错：如果模型遗漏了 analysisTexts 顶层字段，为必需的 schema 填充兜底占位
            if schema is NetworkAgentOutputStrict:
                if "analysisTexts" not in data:
                    data["analysisTexts"] = {
                        "network": {
                            "trustRelations": {"title": "", "content": ""},
                            "suspectRelations": {"title": "", "content": ""},
                            "echoChamber": {"title": "", "content": ""},
                            "avgTrust": {"title": "", "content": ""},
                        }
                    }
                else:
                    net_texts = data.setdefault(
                        "analysisTexts", {}).setdefault("network", {})
                    for key in ("trustRelations", "suspectRelations", "echoChamber", "avgTrust"):
                        net_texts.setdefault(key, {"title": "", "content": ""})

            if schema is PsychologyAgentOutputStrict:
                if "analysisTexts" not in data:
                    data["analysisTexts"] = {
                        "stats": {
                            "cognitiveConsistency": {"title": "", "content": ""},
                            "deceptionScore": {"title": "", "content": ""},
                            "strategyPurity": {"title": "", "content": ""},
                            "stressResponse": {"title": "", "content": ""},
                        },
                        "players": {},
                    }
                else:
                    psy_stats = data.setdefault(
                        "analysisTexts", {}).setdefault("stats", {})
                    for key in ("cognitiveConsistency", "deceptionScore", "strategyPurity", "stressResponse"):
                        psy_stats.setdefault(key, {"title": "", "content": ""})
                    data["analysisTexts"].setdefault("players", {})

            return schema.model_validate(data)
        except (ValueError, json.JSONDecodeError, ValidationError) as exc:
            last_missing = []
            if isinstance(exc, ValidationError):
                try:
                    for e in exc.errors():
                        if e.get("type") == "missing":
                            loc = e.get("loc")
                            if isinstance(loc, (list, tuple)) and loc:
                                last_missing.append(
                                    ".".join(str(x) for x in loc))
                except Exception:
                    last_missing = []
            last_err = str(exc)
            continue

    agent_name = getattr(agent, "name", "<unknown-agent>")
    details = f"LLM 输出无法解析为 {schema.__name__}（agent={agent_name}）: {last_err}"
    if last_raw:
        details += "\n\n最后一次原始输出（已截断/脱敏）：\n" + _excerpt(last_raw)
    raise RuntimeError(details)


PSYCHOLOGY_SYS = """
你是一名“动态心理画像与行为指纹分析”专家。

目标：根据狼人杀游戏日志（含公开发言、心声、反思）与玩家长期经验，总结每位玩家的心理/行为特征，并输出严格 JSON。

硬性要求（务必完全遵守）：
- 只输出一个 JSON 对象，禁止任何解释、提示或 markdown，禁止使用 ```json 代码块。
- 顶层必须同时包含 psychology 与 analysisTexts 字段。
- 所有分数均为 0~1 的小数。
- 指标定义：
    - cognitiveConsistency：心声/反思与公开发言的一致程度（越一致越高）
    - stressResponse：被质疑或压力下的稳定性（越稳定越高）
    - strategyPurity：策略连贯度/稳定性（越稳定越高）
    - expressiveness：表达丰富度、信息量与可理解度（越高越高）
    - deceptionScore：欺骗可疑度（越可疑越高；狼人倾向更高，但允许例外）
- analysisTexts.stats 必须包含且只包含上述 4 项，每项都有 title 与 content（中文）。
- analysisTexts.players: key 为玩家ID（如 Player1），每项都有 title 与 content（中文），尽量覆盖所有玩家。
""".strip()


NETWORK_SYS = """
你是一名“社交网络信任动力学建模”专家。

目标：基于狼人杀日志中的互动、互相站队/质疑、投票同步性，构建信任网络图并输出严格 JSON。

硬性要求（务必完全遵守）：
- 只输出一个 JSON 对象，禁止任何解释、提示或 markdown，禁止使用 ```json 代码块。
- 顶层必须包含 network 与 analysisTexts 字段。
- nodes: 每位玩家一个节点，trust 取值 0~1。
- links: type 只能是 trust/suspect/ally；value 范围 [-1,1]：
    - trust：value>0
    - suspect：value<0
    - ally：value>0（强调同阵营/协同行为）
- 建议链接数量 8~25 条，避免过密。
- analysisTexts.network 必须包含 trustRelations、suspectRelations、echoChamber、avgTrust 四段中文分析文本，每段都有 title 与 content。
""".strip()


class PsychologyAgentOutput(BaseModel):
    psychology: dict[str, Any]
    analysisTexts: dict[str, Any]


class NetworkAgentOutput(BaseModel):
    network: dict[str, Any]
    analysisTexts: dict[str, Any]


class PsychologyTexts(BaseModel):
    stats: StatsAnalysisTexts
    players: dict[str, PlayerAnalysisText]


class PsychologyAgentOutputStrict(BaseModel):
    psychology: Psychology
    analysisTexts: PsychologyTexts


class NetworkTexts(BaseModel):
    network: NetworkAnalysisTexts


class NetworkAgentOutputStrict(BaseModel):
    network: Network
    analysisTexts: NetworkTexts


def build_psychology_prompt(context: dict[str, Any], player_ids: list[str]) -> str:
    return json.dumps(
        {
            "task": "psychology",
            "required": {
                "metrics": [
                    "cognitiveConsistency",
                    "stressResponse",
                    "strategyPurity",
                    "expressiveness",
                    "deceptionScore",
                ],
                "players": player_ids,
                "analysisTexts": {
                    "stats_keys": [
                        "cognitiveConsistency",
                        "deceptionScore",
                        "strategyPurity",
                        "stressResponse",
                    ],
                    "players": player_ids,
                },
            },
            "context": context,
            "output_format": {
                "psychology": {
                    "metrics": [
                        "cognitiveConsistency",
                        "stressResponse",
                        "strategyPurity",
                        "expressiveness",
                        "deceptionScore",
                    ],
                    "players": {
                        "Player1": {
                            "cognitiveConsistency": 0.5,
                            "stressResponse": 0.5,
                            "strategyPurity": 0.5,
                            "expressiveness": 0.5,
                            "deceptionScore": 0.5,
                        }
                    },
                },
                "analysisTexts": {
                    "stats": {
                        "cognitiveConsistency": {"title": "", "content": ""},
                        "deceptionScore": {"title": "", "content": ""},
                        "strategyPurity": {"title": "", "content": ""},
                        "stressResponse": {"title": "", "content": ""},
                    },
                    "players": {
                        "Player1": {"title": "", "content": ""}
                    },
                },
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def build_network_prompt(context: dict[str, Any], player_ids: list[str]) -> str:
    return json.dumps(
        {
            "task": "network",
            "required": {
                "players": player_ids,
                "link_types": ["trust", "suspect", "ally"],
                "analysisTexts": [
                    "trustRelations",
                    "suspectRelations",
                    "echoChamber",
                    "avgTrust",
                ],
            },
            "context": context,
            "output_format": {
                "network": {
                    "nodes": [{"id": "Player1", "group": "villager", "trust": 0.5}],
                    "links": [
                        {
                            "source": "Player1",
                            "target": "Player2",
                            "value": -0.4,
                            "type": "suspect",
                        }
                    ],
                },
                "analysisTexts": {
                    "network": {
                        "trustRelations": {"title": "", "content": ""},
                        "suspectRelations": {"title": "", "content": ""},
                        "echoChamber": {"title": "", "content": ""},
                        "avgTrust": {"title": "", "content": ""},
                    }
                },
            },
        },
        ensure_ascii=False,
        indent=2,
    )
