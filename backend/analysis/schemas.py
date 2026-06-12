# -*- coding: utf-8 -*-
"""用于 report_demo.html 中 analysisData 的 Pydantic 数据结构定义。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


Role = Literal["villager", "witch", "seer", "hunter", "werewolf"]
LinkType = Literal["trust", "suspect", "ally"]


class PlayerInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: Role
    isWerewolf: bool


class StatAnalysisText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    content: str


class PlayerAnalysisText(StatAnalysisText):
    pass


class NetworkAnalysisTexts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trustRelations: StatAnalysisText
    suspectRelations: StatAnalysisText
    echoChamber: StatAnalysisText
    avgTrust: StatAnalysisText


class StatsAnalysisTexts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cognitiveConsistency: StatAnalysisText
    deceptionScore: StatAnalysisText
    strategyPurity: StatAnalysisText
    stressResponse: StatAnalysisText


class AnalysisTexts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stats: StatsAnalysisTexts
    players: dict[str, PlayerAnalysisText] = Field(default_factory=dict)
    network: NetworkAnalysisTexts


class PsychologyPlayerMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cognitiveConsistency: float = Field(ge=0.0, le=1.0)
    stressResponse: float = Field(ge=0.0, le=1.0)
    strategyPurity: float = Field(ge=0.0, le=1.0)
    expressiveness: float = Field(ge=0.0, le=1.0)
    deceptionScore: float = Field(ge=0.0, le=1.0)


class Psychology(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metrics: list[str]
    players: dict[str, PsychologyPlayerMetrics]


class NetworkNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    group: Role
    trust: float = Field(ge=0.0, le=1.0)


class NetworkLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    value: float = Field(ge=-1.0, le=1.0)
    type: LinkType


class Network(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[NetworkNode]
    links: list[NetworkLink]


class AnalysisData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gameId: str
    players: list[PlayerInfo]
    analysisTexts: AnalysisTexts
    psychology: Psychology
    network: Network
