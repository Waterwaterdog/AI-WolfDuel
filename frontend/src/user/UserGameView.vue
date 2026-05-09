<template>
  <div class="user-shell">
    <div class="header">
      <Header
        :status-text="statusText"
        :phase-text="phaseText"
        :username="username"
        :on-start-game="startGame"
        :on-stop-game="handleStopGame"
        :start-disabled="startingGame || isGameRunning"
        :stop-disabled="stoppingGame || !isGameRunning"
        :on-export-log="handleExportLog"
        :on-export-experience="handleExportExperience"
        :export-log-disabled="exportingLog"
        :export-experience-disabled="exportingExperience"
        :on-logout="onLogout"
        logout-label="退出游戏"
      />
    </div>

    <div class="user-layout">
      <div class="user-room">
        <div class="user-room__board" :class="phaseClass">
          <div class="user-room__topbar">
            <div class="user-room__role">
              <span>你的身份</span>
              <strong>{{ humanRoleDisplay }}</strong>
            </div>
            <button class="avatar-upload-btn" type="button" @click="pickAvatar">
              更换 1 号头像
            </button>
            <input ref="avatarInputRef" type="file" accept="image/*" hidden @change="handleAvatarChange" />
          </div>

          <div class="user-room__grid">
            <button
              v-for="agent in agents"
              :key="agent.id"
              type="button"
              class="seat-card"
              :class="{
                'seat-card--dead': agent.alive === false,
                'seat-card--human': agent.id === 'player_1',
                'seat-card--wolfmate': agent.wolfmate,
                'seat-card--good': agent.knownAlignment === '好人',
                'seat-card--wolf': agent.knownAlignment === '狼人',
              }"
              @click="handleSeatClick(agent)"
            >
              <div class="seat-card__avatar-wrap">
                <img class="seat-card__avatar" :src="agent.avatar" :alt="agent.name" />
                <span v-if="agent.id === 'player_1'" class="seat-card__badge">你</span>
                <span v-if="agent.wolfmate" class="seat-card__mark">狼队友</span>
                <span v-if="agent.knownAlignment === '好人'" class="seat-card__mark seat-card__mark--good">好人</span>
                <span v-if="agent.knownAlignment === '狼人'" class="seat-card__mark seat-card__mark--wolf">狼人</span>
              </div>
              <div class="seat-card__name">{{ agent.name }}</div>
              <div class="seat-card__meta">
                {{ displayRoleLabel(agent) }}
              </div>
              <div v-if="shouldShowCardBubble(agent.id) && bubbleFor(agent.id)" class="seat-card__bubble">
                {{ bubbleFor(agent.id).text }}
              </div>
            </button>
          </div>

          <div v-if="nightBlindVisible" class="night-blind">
            <span>天黑请闭眼</span>
          </div>
          <div v-if="overlay.visible" class="overlay-card">
            <div class="overlay-card__title">{{ overlay.title }}</div>
            <div class="overlay-card__content">{{ overlay.content }}</div>
          </div>
          <div v-if="shouldShowNightPrompt" class="user-room__night-prompt">
            {{ nightPrompt.text }}
          </div>
        </div>
      </div>

      <div class="user-feed">
        <GameFeed :feed="feed" :hide-thoughts="true" />
      </div>
    </div>

    <div v-if="pendingAction" class="action-modal">
      <div class="action-modal__panel" :class="pendingAction.side === 'left' ? 'action-modal__panel--left' : ''">
        <div class="action-modal__title">{{ pendingAction.title }}</div>
        <div class="action-modal__prompt">{{ pendingAction.prompt }}</div>

        <template v-if="pendingAction.inputMode === 'text'">
          <textarea
            v-model="actionText"
            class="action-modal__textarea"
            :placeholder="pendingAction.placeholder || '请输入内容'"
          />
        </template>

        <template v-else>
          <div class="action-modal__options">
            <button
              v-for="option in pendingAction.options || []"
              :key="option.value"
              type="button"
              class="action-modal__option"
              :class="{ 'is-selected': actionChoice === option.value }"
              @click="actionChoice = option.value"
            >
              {{ option.label }}
            </button>
          </div>
        </template>

        <div class="action-modal__actions">
          <button type="button" class="action-modal__submit" :disabled="submittingAction" @click="submitAction">
            {{ submittingAction ? "提交中…" : "确认" }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import Header from "../components/Header.vue";
import GameFeed from "../components/GameFeed.vue";
import { ASSETS, BUBBLE_LIFETIME_MS, TYPING_LIFETIME_MS, DEFAULT_AGENTS } from "../config/constants";
import { useFeedProcessor } from "../hooks/useFeedProcessor";
import { ReadOnlyClient } from "../services/websocket";
import {
  exportExperience,
  exportLog,
  fetchGameStatus,
  fetchPendingHumanAction,
  startUserGame,
  stopGame,
  submitHumanAction,
} from "./api";

defineProps({
  username: { type: String, default: "" },
  onLogout: { type: Function, default: null },
});

const agents = ref(JSON.parse(JSON.stringify(DEFAULT_AGENTS)));
const avatarInputRef = ref(null);
const connectionStatus = ref("connecting");
const phaseText = ref("准备中");
const gameStatus = ref({ status: "idle", mode: "user", gameId: null, logPath: null, experiencePath: null });
const pendingAction = ref(null);
const actionText = ref("");
const actionChoice = ref("");
const submittingAction = ref(false);
const startingGame = ref(false);
const stoppingGame = ref(false);
const exportingLog = ref(false);
const exportingExperience = ref(false);
const humanState = ref(null);
const overlay = ref({ visible: false, title: "", content: "" });
const nightBlindVisible = ref(false);
const nightPrompt = ref({ text: "", timestamp: 0 });
const bubbles = ref({});
const bubbleTimersRef = ref({});
const clientRef = ref(null);
let statusTimer = null;
let pendingTimer = null;
let overlayTimer = null;
let nightPromptTimer = null;

const getAgentById = (agentId) => agents.value.find((item) => item.id === agentId) || null;
const { feed, processHistoricalFeed, processFeedEvent, addSystemMessage, resetFeed } = useFeedProcessor({ getAgentById });

const statusText = computed(() => {
  if (connectionStatus.value === "connected") return "已连接";
  if (connectionStatus.value === "disconnected") return "连接断开";
  return "连接中";
});

const phaseClass = computed(() => (String(phaseText.value).includes("夜") ? "is-night" : "is-day"));
const isGameRunning = computed(() => String(gameStatus.value?.status || "").toLowerCase() === "running");
const humanRoleDisplay = computed(() => humanState.value?.roleDisplay || "未知");
const shouldShowNightPrompt = computed(() => phaseClass.value === "is-night" && !nightBlindVisible.value && Boolean(nightPrompt.value.text));

const mapPlayerNameToAgentId = (name) => {
  const text = String(name || "");
  if (!text.toLowerCase().startsWith("player")) return null;
  const num = Number(text.slice(6));
  return Number.isFinite(num) ? `player_${num}` : null;
};

const bubbleFor = (id) => bubbles.value[id] || null;
const shouldShowCardBubble = () => phaseClass.value !== "is-night";

const displayRoleLabel = (agent) => {
  if (!agent) return "未知身份";
  if (agent.id === "player_1") return humanRoleDisplay.value;
  if (agent.knownAlignment) return agent.knownAlignment;
  return agent.revealedRole || "未知身份";
};

const setNightPrompt = (text, durationMs = 3000) => {
  const value = String(text || "").trim();
  if (!value) return;
  nightPrompt.value = { text: value, timestamp: Date.now() };
  if (nightPromptTimer) clearTimeout(nightPromptTimer);
  nightPromptTimer = setTimeout(() => {
    nightPrompt.value = { text: "", timestamp: 0 };
  }, durationMs);
};

const canDisplayScopedEvent = (evt) => {
  const scope = String(evt?.scope || "public");
  if (scope === "hidden") return false;
  if (scope === "human_only") return true;
  if (scope === "wolves_only") return humanRoleDisplay.value === "狼人";
  return true;
};

const showOverlay = (title, content, durationMs = 2000) => {
  overlay.value = { visible: true, title, content };
  if (overlayTimer) clearTimeout(overlayTimer);
  overlayTimer = setTimeout(() => {
    overlay.value = { visible: false, title: "", content: "" };
  }, durationMs);
};

const applyPlayersInit = (players) => {
  if (!Array.isArray(players)) return;
  const byId = new Map(agents.value.map((agent) => [agent.id, agent]));
  for (const player of players) {
    const agentId = mapPlayerNameToAgentId(player?.name);
    if (!agentId) continue;
    const base = byId.get(agentId) || { id: agentId, name: agentId };
    const visibleRole = player?.role || "未知身份";
    byId.set(agentId, {
      ...base,
      id: agentId,
      name: `${Number(agentId.replace("player_", ""))}号`,
      revealedRole: agentId === "player_1" ? (humanState.value?.roleDisplay || "未知身份") : visibleRole,
      role: visibleRole,
      avatar: agentId === "player_1" ? (base.customAvatar || base.avatar || ASSETS.avatars.villager) : ASSETS.avatars.villager,
      alive: player?.alive !== false,
      alignment: player?.alignment || base.alignment || "unknown",
      wolfmate: false,
      knownAlignment: "",
    });
  }
  agents.value = Array.from(byId.values()).sort((a, b) => Number(a.id.replace("player_", "")) - Number(b.id.replace("player_", "")));
};

const applyHumanState = (payload) => {
  humanState.value = payload?.human || null;
  const players = Array.isArray(payload?.players) ? payload.players : [];
  if (!players.length) return;
  const knownMap = new Map((humanState.value?.checkedIdentities && Object.entries(humanState.value.checkedIdentities)) || []);
  const wolfTeammates = new Set(humanState.value?.wolfTeammates || []);
  agents.value = agents.value.map((agent) => {
    const playerInfo = players.find((item) => mapPlayerNameToAgentId(item.name) === agent.id);
    const seatName = playerInfo?.name || `Player${agent.id.replace("player_", "")}`;
    const knownAlignment = knownMap.get(seatName) || "";
    const isHuman = agent.id === "player_1";
    const isWolfmate = wolfTeammates.has(seatName);
    const visibleRole = isHuman
      ? (humanState.value?.roleDisplay || "未知身份")
      : (knownAlignment || playerInfo?.role || (isWolfmate ? "狼人" : "未知身份"));
    return {
      ...agent,
      role: visibleRole,
      revealedRole: visibleRole,
      avatar: isHuman ? (agent.customAvatar || agent.avatar || ASSETS.avatars.villager) : ASSETS.avatars.villager,
      alive: playerInfo?.alive !== false,
      alignment: isHuman ? (humanState.value?.alignment || agent.alignment || "unknown") : (playerInfo?.alignment || agent.alignment || "unknown"),
      wolfmate: isWolfmate,
      knownAlignment,
    };
  });
};

const markPlayersDead = (deadPlayerNames) => {
  if (!Array.isArray(deadPlayerNames)) return;
  const deadIds = new Set(deadPlayerNames.map((item) => mapPlayerNameToAgentId(item)).filter(Boolean));
  agents.value = agents.value.map((agent) => (
    deadIds.has(agent.id) ? { ...agent, alive: false } : agent
  ));
};

const extractBubbleText = (content) => String(content || "")
  .replace(/<history>[\s\S]*?<\/history>/gi, " ")
  .replace(/<think>[\s\S]*?<\/think>/gi, " ")
  .replace(/<\/?(history|think)>/gi, " ")
  .replace(/^\s*(speech|behavior|thought)\s*:\s*/gim, "")
  .replace(/^[\"']+|[\"']+$/g, "")
  .trim();

const sanitizePublicOnlyText = (text) => {
  const source = String(text || "").trim();
  if (!source) return "";

  const looksPrivate = (segment) => {
    const normalized = String(segment || "").replace(/\s+/g, "");
    if (!normalized) return false;

    const directMarkers = [
      "我的发言将",
      "我应该保持",
      "我需要保持",
      "我的目的是",
      "在这一轮的讨论中",
      "我会谨慎地",
      "我会保持冷静",
      "不想暴露自己的身份",
      "不暴露自己的身份",
      "避免暴露自己的身份",
      "避免过早暴露自己的身份",
      "隐藏自己的身份",
      "隐藏我自己的身份",
      "保持低调",
      "狼队友",
    ];
    if (directMarkers.some((marker) => normalized.includes(marker))) {
      return true;
    }

    if (normalized.includes("身份") && /(暴露|隐藏|伪装|低调)/.test(normalized)) {
      return true;
    }

    return /(?:作为|身为)(?:一名)?(?:狼人|女巫|预言家|猎人|村民|平民).{0,24}(?:不想|不会|不能|需要|必须|打算|希望|准备|避免|暴露|隐藏|伪装|低调|观察)/.test(normalized);
  };

  const segments = source.split(/\n\s*\n|\n/).map((segment) => segment.trim()).filter(Boolean);
  const publicParts = [];
  for (const segment of segments) {
    if (looksPrivate(segment)) {
      if (publicParts.length) break;
      continue;
    }
    publicParts.push(segment);
  }

  return (publicParts.join("\n") || segments[0] || "").trim();
};

const upsertBubbleFromMessage = (evt) => {
  if (!evt?.agentId) return;
  const behaviorText = String(evt.behavior || "").trim();
  const speechText = sanitizePublicOnlyText(String(evt.speech || evt.content || "").trim());
  const text = [behaviorText ? `(表现) ${extractBubbleText(behaviorText)}` : "", speechText ? `(发言) ${extractBubbleText(speechText)}` : ""]
    .filter(Boolean)
    .join("\n");
  if (phaseClass.value === "is-night") {
    setNightPrompt(text || sanitizePublicOnlyText(extractBubbleText(evt.content || "")), BUBBLE_LIFETIME_MS);
    return;
  }
  bubbles.value = {
    ...bubbles.value,
    [evt.agentId]: {
      agentId: evt.agentId,
      text: text || sanitizePublicOnlyText(extractBubbleText(evt.content || "")),
      timestamp: evt.timestamp || Date.now(),
    },
  };
  if (bubbleTimersRef.value[evt.agentId]) clearTimeout(bubbleTimersRef.value[evt.agentId]);
  bubbleTimersRef.value[evt.agentId] = setTimeout(() => {
    const next = { ...bubbles.value };
    delete next[evt.agentId];
    bubbles.value = next;
    delete bubbleTimersRef.value[evt.agentId];
  }, BUBBLE_LIFETIME_MS);
};

const handleAgentTyping = (evt) => {
  if (!evt?.agentId) return;
  if (phaseClass.value === "is-night") {
    setNightPrompt(evt.categoryDisplay || "夜间行动中…", TYPING_LIFETIME_MS);
    return;
  }
  bubbles.value = {
    ...bubbles.value,
    [evt.agentId]: {
      agentId: evt.agentId,
      text: evt.categoryDisplay || "思考中…",
      isTyping: true,
      timestamp: evt.timestamp || Date.now(),
    },
  };
  if (bubbleTimersRef.value[evt.agentId]) clearTimeout(bubbleTimersRef.value[evt.agentId]);
  bubbleTimersRef.value[evt.agentId] = setTimeout(() => {
    const next = { ...bubbles.value };
    if (next[evt.agentId]?.isTyping) delete next[evt.agentId];
    bubbles.value = next;
    delete bubbleTimersRef.value[evt.agentId];
  }, TYPING_LIFETIME_MS);
};

const resetRuntimeState = () => {
  phaseText.value = "准备中";
  gameStatus.value = { status: "idle", mode: "user", gameId: null, logPath: null, experiencePath: null };
  agents.value = JSON.parse(JSON.stringify(DEFAULT_AGENTS));
  humanState.value = null;
  overlay.value = { visible: false, title: "", content: "" };
  nightBlindVisible.value = false;
  nightPrompt.value = { text: "", timestamp: 0 };
  pendingAction.value = null;
  actionText.value = "";
  actionChoice.value = "";
  bubbles.value = {};
  resetFeed();
};

const refreshStatus = async () => {
  try {
    const data = await fetchGameStatus();
    gameStatus.value = data;
  } catch {
    // ignore
  }
};

const pollPendingAction = async () => {
  try {
    const data = await fetchPendingHumanAction();
    if (!data?.pending) {
      pendingAction.value = null;
      actionText.value = "";
      actionChoice.value = "";
      return;
    }
    if (pendingAction.value?.id !== data.pending.id) {
      pendingAction.value = data.pending;
      actionText.value = "";
      actionChoice.value = "";
    }
  } catch {
    // ignore
  }
};

const startRealtime = () => {
  if (statusTimer) clearInterval(statusTimer);
  if (pendingTimer) clearInterval(pendingTimer);
  statusTimer = setInterval(refreshStatus, 1500);
  pendingTimer = setInterval(pollPendingAction, 800);
  const client = new ReadOnlyClient((evt) => {
    if (!evt?.type) return;
    if (evt.type === "system" && String(evt.content || "").toLowerCase().includes("connected")) {
      connectionStatus.value = "connected";
    }
    if (evt.type === "system" && String(evt.content || "").toLowerCase().includes("try to connect")) {
      connectionStatus.value = "disconnected";
    }
    if (evt.type === "day_start") {
      phaseText.value = "白天";
      nightBlindVisible.value = false;
      nightPrompt.value = { text: "", timestamp: 0 };
    }
    if (evt.type === "night_start") {
      phaseText.value = "夜晚";
      nightPrompt.value = { text: "", timestamp: 0 };
    }
    if (evt.type === "overlay") showOverlay(evt.title || "提示", evt.content || "", evt.durationMs || 2000);
    if (evt.type === "human_role_reveal") showOverlay("你的身份", `你的身份是${evt.roleDisplay}`, evt.durationMs || 3000);
    if (evt.type === "human_role_state") applyHumanState(evt);
    if (evt.type === "human_action_required") {
      pendingAction.value = evt.action;
      actionText.value = "";
      actionChoice.value = "";
    }
    if (evt.type === "human_action_cleared") {
      pendingAction.value = null;
      actionText.value = "";
      actionChoice.value = "";
    }
    if (evt.type === "system" && Array.isArray(evt.players) && !String(evt.category || "").includes("死亡")) {
      applyPlayersInit(evt.players);
    }
    if (evt.type === "system" && Array.isArray(evt.players) && String(evt.category || "").includes("死亡")) {
      markPlayersDead(evt.players);
    }
    if (evt.type === "historical" && Array.isArray(evt.events)) {
      const history = [...evt.events].reverse();
      for (const item of history) {
        if (item.type === "human_role_state") applyHumanState(item);
        if (item.type === "overlay") showOverlay(item.title || "提示", item.content || "", item.durationMs || 2000);
      }
      const playersInit = history.find((item) => item.type === "system" && Array.isArray(item.players));
      if (playersInit?.players) applyPlayersInit(playersInit.players);
      processHistoricalFeed(evt.events.filter((item) => canDisplayScopedEvent(item)));
      return;
    }

    if (!canDisplayScopedEvent(evt)) {
      if (evt.scope === "wolves_only" && (evt.type === "agent_message" || evt.type === "agent_typing" || evt.type === "system")) {
        nightBlindVisible.value = String(phaseText.value).includes("夜");
      }
      return;
    }

    if (evt.scope === "wolves_only" && humanRoleDisplay.value !== "狼人") {
      if (evt.type === "agent_message" || evt.type === "agent_typing" || evt.type === "system") {
        nightBlindVisible.value = String(phaseText.value).includes("夜");
      }
      return;
    }

    if (evt.type === "agent_message" || evt.type === "conference_message") upsertBubbleFromMessage(evt);
    if (evt.type === "agent_typing") handleAgentTyping(evt);
    if (evt.type === "system" || evt.type === "agent_message" || evt.type === "conference_message" || evt.type === "day_start" || evt.type === "night_start" || evt.type === "round_start" || evt.type === "day_error") {
      processFeedEvent(evt);
    }
  });
  clientRef.value = client;
  client.connect();
  refreshStatus();
  pollPendingAction();
};

const stopRealtime = () => {
  if (statusTimer) clearInterval(statusTimer);
  if (pendingTimer) clearInterval(pendingTimer);
  statusTimer = null;
  pendingTimer = null;
  if (clientRef.value) {
    clientRef.value.disconnect();
    clientRef.value = null;
  }
  Object.values(bubbleTimersRef.value).forEach((timer) => clearTimeout(timer));
  bubbleTimersRef.value = {};
  if (nightPromptTimer) clearTimeout(nightPromptTimer);
  nightPromptTimer = null;
};

const pickAvatar = () => avatarInputRef.value?.click();

const handleAvatarChange = (event) => {
  const file = event.target?.files?.[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    agents.value = agents.value.map((agent) => (
      agent.id === "player_1" ? { ...agent, avatar: reader.result, customAvatar: reader.result } : agent
    ));
  };
  reader.readAsDataURL(file);
  event.target.value = "";
};

const handleSeatClick = (agent) => {
  if (agent.id === "player_1") {
    pickAvatar();
  }
};

const downloadResponse = async (res, fallbackName) => {
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `${res.status}`);
  }
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = fallbackName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
};

const startGame = async () => {
  if (startingGame.value) return;
  startingGame.value = true;
  try {
    await startUserGame();
    addSystemMessage("用户模式游戏已开始。");
    await refreshStatus();
  } catch (error) {
    addSystemMessage(String(error?.message || error));
  } finally {
    startingGame.value = false;
  }
};

const handleStopGame = async () => {
  if (stoppingGame.value) return;
  stoppingGame.value = true;
  try {
    await stopGame();
    addSystemMessage("已请求终止游戏。");
    await refreshStatus();
  } catch (error) {
    addSystemMessage(String(error?.message || error));
  } finally {
    stoppingGame.value = false;
  }
};

const handleExportLog = async () => {
  if (exportingLog.value) return;
  exportingLog.value = true;
  try {
    await downloadResponse(await exportLog(), "game.log");
  } finally {
    exportingLog.value = false;
  }
};

const handleExportExperience = async () => {
  if (exportingExperience.value) return;
  exportingExperience.value = true;
  try {
    await downloadResponse(await exportExperience(), "experience.json");
  } finally {
    exportingExperience.value = false;
  }
};

const submitAction = async () => {
  if (!pendingAction.value || submittingAction.value) return;
  submittingAction.value = true;
  try {
    await submitHumanAction({
      actionId: pendingAction.value.id,
      choice: actionChoice.value || null,
      text: actionText.value || null,
    });
    pendingAction.value = null;
    actionText.value = "";
    actionChoice.value = "";
  } catch (error) {
    addSystemMessage(String(error?.message || error));
  } finally {
    submittingAction.value = false;
  }
};

onMounted(() => {
  agents.value = agents.value.map((agent) => (
    agent.id === "player_1" ? { ...agent, avatar: ASSETS.avatars.villager } : agent
  ));
  startRealtime();
});

onBeforeUnmount(() => {
  stopRealtime();
  resetRuntimeState();
  if (overlayTimer) clearTimeout(overlayTimer);
});
</script>

<style scoped>
.user-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: linear-gradient(180deg, #020617, #0f172a 55%, #111827);
}

.user-layout {
  flex: 1;
  min-height: 0;
  display: flex;
}

.user-room {
  width: 62%;
  padding: 16px;
}

.user-feed {
  width: 38%;
  min-width: 360px;
  border-left: 1px solid rgba(148, 163, 184, 0.16);
  background: rgba(15, 23, 42, 0.66);
}

.user-room__board {
  position: relative;
  min-height: calc(100vh - 92px);
  border-radius: 28px;
  overflow: hidden;
  border: 1px solid rgba(148, 163, 184, 0.18);
  padding: 20px;
  background-size: cover;
  background-position: center;
}

.user-room__board.is-day {
  background-image: linear-gradient(rgba(7, 89, 133, 0.28), rgba(15, 23, 42, 0.35)), url("/theme/day-castle.jpg");
}

.user-room__board.is-night {
  background-image: linear-gradient(rgba(2, 6, 23, 0.32), rgba(15, 23, 42, 0.72)), url("/theme/night-castle.jpg");
}

.user-room__topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  gap: 12px;
}

.user-room__role {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.78);
  color: #e2e8f0;
}

.avatar-upload-btn {
  border: 0;
  border-radius: 999px;
  padding: 10px 16px;
  background: linear-gradient(135deg, #2563eb, #4338ca);
  color: #eff6ff;
  font-weight: 700;
  cursor: pointer;
}

.user-room__grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}

.seat-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 10px;
  border: 1px solid rgba(148, 163, 184, 0.24);
  border-radius: 22px;
  padding: 16px;
  min-height: 180px;
  background: rgba(255, 255, 255, 0.86);
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease;
}

.seat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 16px 40px rgba(15, 23, 42, 0.16);
}

.seat-card--human {
  border-color: rgba(59, 130, 246, 0.55);
  box-shadow: inset 0 0 0 1px rgba(96, 165, 250, 0.32);
}

.seat-card--dead {
  opacity: 0.56;
  filter: grayscale(1);
}

.seat-card--wolfmate {
  border-color: rgba(185, 28, 28, 0.66);
  box-shadow: inset 0 0 0 1px rgba(239, 68, 68, 0.26);
}

.seat-card--good {
  border-color: rgba(22, 163, 74, 0.66);
}

.seat-card--wolf {
  border-color: rgba(220, 38, 38, 0.72);
}

.seat-card__avatar-wrap {
  position: relative;
  display: flex;
  justify-content: center;
}

.seat-card__avatar {
  width: 82px;
  height: 82px;
  border-radius: 999px;
  object-fit: cover;
  background: #fff;
  border: 4px solid rgba(255, 255, 255, 0.92);
}

.seat-card__badge,
.seat-card__mark {
  position: absolute;
  top: -6px;
  right: 6px;
  padding: 4px 8px;
  border-radius: 999px;
  background: #1d4ed8;
  color: #fff;
  font-size: 12px;
  font-weight: 700;
}

.seat-card__mark {
  top: auto;
  bottom: -8px;
  right: auto;
  left: 50%;
  transform: translateX(-50%);
  background: #991b1b;
}

.seat-card__mark--good {
  background: #15803d;
}

.seat-card__mark--wolf {
  background: #b91c1c;
}

.seat-card__name {
  text-align: center;
  font-size: 18px;
  font-weight: 800;
  color: #111827;
}

.seat-card__meta {
  text-align: center;
  color: #475569;
  font-size: 13px;
}

.seat-card__bubble {
  margin-top: auto;
  padding: 10px 12px;
  border-radius: 14px;
  background: rgba(248, 250, 252, 0.95);
  border: 1px solid rgba(203, 213, 225, 0.9);
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.6;
  color: #0f172a;
}

.overlay-card {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 14px;
  background: rgba(2, 6, 23, 0.62);
  color: #f8fafc;
  z-index: 20;
}

.overlay-card__title {
  font-size: 28px;
  font-weight: 900;
}

.overlay-card__content {
  max-width: 680px;
  white-space: pre-wrap;
  text-align: center;
  font-size: 20px;
  line-height: 1.8;
}

.user-room__night-prompt {
  position: absolute;
  left: 24px;
  right: 24px;
  bottom: 24px;
  z-index: 19;
  padding: 14px 18px;
  border-radius: 18px;
  background: rgba(2, 6, 23, 0.86);
  border: 1px solid rgba(148, 163, 184, 0.24);
  color: #f8fafc;
  text-align: center;
  white-space: pre-wrap;
  line-height: 1.7;
  box-shadow: 0 18px 45px rgba(15, 23, 42, 0.32);
}

.night-blind {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(2, 6, 23, 0.82);
  color: #f8fafc;
  font-size: 52px;
  font-weight: 900;
  letter-spacing: 0.12em;
  z-index: 18;
}

.action-modal {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: rgba(2, 6, 23, 0.55);
}

.action-modal__panel {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  width: min(560px, calc(100vw - 32px));
  background: rgba(255, 255, 255, 0.98);
  border-radius: 24px;
  padding: 22px;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.28);
}

.action-modal__panel--left {
  left: 24px;
  top: 50%;
  transform: translateY(-50%);
}

.action-modal__title {
  font-size: 22px;
  font-weight: 900;
  color: #111827;
}

.action-modal__prompt {
  margin-top: 10px;
  color: #475569;
  line-height: 1.7;
}

.action-modal__textarea {
  width: 100%;
  min-height: 140px;
  margin-top: 16px;
  resize: vertical;
  padding: 14px 16px;
  border-radius: 16px;
  border: 1px solid #cbd5e1;
  outline: none;
}

.action-modal__options {
  margin-top: 18px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.action-modal__option {
  border: 1px solid #cbd5e1;
  background: #f8fafc;
  color: #0f172a;
  padding: 12px;
  border-radius: 14px;
  cursor: pointer;
}

.action-modal__option.is-selected {
  border-color: #2563eb;
  background: #dbeafe;
}

.action-modal__actions {
  margin-top: 18px;
  display: flex;
  justify-content: flex-end;
}

.action-modal__submit {
  border: 0;
  border-radius: 999px;
  padding: 12px 18px;
  background: linear-gradient(135deg, #2563eb, #4338ca);
  color: #fff;
  font-weight: 800;
  cursor: pointer;
}

@media (max-width: 1100px) {
  .user-layout {
    flex-direction: column;
  }

  .user-room,
  .user-feed {
    width: 100%;
  }

  .user-feed {
    min-width: 0;
    border-left: 0;
    border-top: 1px solid rgba(148, 163, 184, 0.16);
  }
}

@media (max-width: 780px) {
  .user-room__grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .night-blind {
    font-size: 34px;
  }
}
</style>
