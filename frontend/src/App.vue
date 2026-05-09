<template>
  <div v-if="!isAuthenticated" class="login-screen">
    <div class="login-screen__veil" />
    <span
      v-for="spark in loginSparks"
      :key="spark.id"
      class="login-screen__spark"
      :style="{
        left: spark.left,
        top: spark.top,
        animationDelay: spark.delay,
        animationDuration: spark.duration,
      }"
    />

    <div class="login-panel">
      <div class="login-panel__crest">Magic Castle</div>
      <h1 class="login-panel__title">天黑请闭眼</h1>
      <p class="login-panel__subtitle">欢迎来到我们的狼人杀游戏</p>

      <form class="login-form" @submit.prevent="handleLogin">
        <label class="login-form__field">
          <span>用户名</span>
          <input
            v-model.trim="loginForm.username"
            type="text"
            autocomplete="username"
            placeholder="请输入用户名"
          />
        </label>

        <label class="login-form__field">
          <span>密码</span>
          <input
            v-model="loginForm.password"
            type="password"
            autocomplete="current-password"
            placeholder="请输入密码"
          />
        </label>

        <p v-if="loginError" class="login-form__error">
          {{ loginError }}
        </p>

        <button class="login-form__submit" type="submit" :disabled="isLoggingIn">
          {{ isLoggingIn ? "进入中…" : "进入游戏" }}
        </button>
      </form>

      <div class="login-panel__tips">
        <span>测试账号：</span>
        <span>user / user123</span>
        <span>admin / admin123</span>
      </div>
    </div>
  </div>

  <UserGameView
    v-else-if="authenticatedUser === 'user'"
    :username="authenticatedUser"
    :on-logout="handleLogout"
  />

  <div v-else class="app app--game">
    <div class="header">
      <Header
        :status-text="statusText"
        :phase-text="phaseText"
        :username="authenticatedUser"
        :on-start-game="startGame"
        :on-stop-game="stopGame"
        :start-disabled="startingGame || isGameRunning"
        :start-label="startingGame ? '启动中…' : '开始游戏'"
        :stop-disabled="stoppingGame || !isGameRunning"
        :stop-label="stoppingGame ? '终止中…' : '终止游戏'"
        :on-export-log="exportLog"
        :export-log-disabled="exportingLog"
        :export-log-label="exportingLog ? '导出中…' : '导出日志'"
        :on-export-experience="exportExperience"
        :export-experience-disabled="exportingExperience"
        :export-experience-label="exportingExperience ? '导出中…' : '导出经验'"
        :on-logout="handleLogout"
        logout-label="退出游戏"
      />
    </div>

    <div class="game-layout">
      <div class="game-layout__room">
        <RoomView
          :agents="agents"
          :bubbles="bubbles"
          :bubble-for="bubbleFor"
          :leaderboard="[]"
          :feed="feed"
          :phase-text="phaseText"
          :on-jump-to-message="handleJumpToMessage"
        />
      </div>
      <div class="game-layout__feed">
        <GameFeed ref="feedRef" :feed="feed" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";
import Header from "./components/Header.vue";
import RoomView from "./components/RoomView.vue";
import GameFeed from "./components/GameFeed.vue";
import UserGameView from "./user/UserGameView.vue";

import { DEFAULT_AGENTS, API_URL, BUBBLE_LIFETIME_MS, TYPING_LIFETIME_MS, ASSETS } from "./config/constants";
import { ReadOnlyClient } from "./services/websocket";
import { useFeedProcessor } from "./hooks/useFeedProcessor";

const AUTH_STORAGE_KEY = "wolfagents_auth_user";
const VALID_USERS = {
  user: "user123",
  admin: "admin123",
};

const loginSparks = [
  { id: 1, left: "10%", top: "16%", delay: "0s", duration: "4.8s" },
  { id: 2, left: "22%", top: "72%", delay: "1.2s", duration: "5.4s" },
  { id: 3, left: "36%", top: "28%", delay: "0.6s", duration: "4.6s" },
  { id: 4, left: "61%", top: "18%", delay: "2.2s", duration: "5.8s" },
  { id: 5, left: "73%", top: "67%", delay: "1.8s", duration: "4.9s" },
  { id: 6, left: "86%", top: "34%", delay: "2.8s", duration: "5.1s" },
];

const extractBubbleText = (content) => {
  const text = String(content || "");
  return text.replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
};

const authenticatedUser = ref(window.localStorage.getItem(AUTH_STORAGE_KEY) || "");
const loginForm = ref({ username: "", password: "" });
const loginError = ref("");
const isLoggingIn = ref(false);

const connectionStatus = ref("connecting");
const phaseText = ref("准备中");
const startingGame = ref(false);
const stoppingGame = ref(false);
const exportingLog = ref(false);
const exportingExperience = ref(false);

const gameStatus = ref({ status: "idle", gameId: null, logPath: null, experiencePath: null });

const agents = ref([...DEFAULT_AGENTS]);

const getAgentById = (agentId) => {
  if (!agentId) return null;
  return agents.value.find((a) => a.id === agentId) || null;
};

const { feed, processHistoricalFeed, processFeedEvent, addSystemMessage, resetFeed } = useFeedProcessor({ getAgentById });

const bubbles = ref({});
const clientRef = ref(null);
const feedRef = ref(null);
const bubbleTimersRef = ref({});

const roleMetaFromRole = (role) => {
  const raw = String(role || "").toLowerCase();
  const isWerewolf = raw.includes("狼人") || raw.includes("werewolf");
  const isSeer = raw.includes("预言家") || raw.includes("seer") || raw.includes("prophet");
  const isWitch = raw.includes("女巫") || raw.includes("witch");
  const isHunter = raw.includes("猎人") || raw.includes("hunter");
  const isGuard = raw.includes("守卫") || raw.includes("guard") || raw.includes("bodyguard");
  const isVillager = raw.includes("平民") || raw.includes("villager") || raw.includes("村民");

  if (isWerewolf) {
    return {
      alignment: "werewolves",
      avatar: ASSETS.avatars.werewolf,
      colors: { bg: "#111827", text: "#F8FAFC", accent: "#F8FAFC" },
    };
  }
  if (isSeer) {
    return {
      alignment: "villagers",
      avatar: ASSETS.avatars.seer,
      colors: { bg: "#EEF2FF", text: "#3730A3", accent: "#3730A3" },
    };
  }
  if (isWitch) {
    return {
      alignment: "villagers",
      avatar: ASSETS.avatars.witch,
      colors: { bg: "#ECFEFF", text: "#0F766E", accent: "#0F766E" },
    };
  }
  if (isHunter) {
    return {
      alignment: "villagers",
      avatar: ASSETS.avatars.hunter,
      colors: { bg: "#FEF3C7", text: "#92400E", accent: "#92400E" },
    };
  }
  if (isGuard) {
    return {
      alignment: "villagers",
      avatar: ASSETS.avatars.guard,
      colors: { bg: "#ECFDF5", text: "#065F46", accent: "#065F46" },
    };
  }
  if (isVillager) {
    return {
      alignment: "villagers",
      avatar: ASSETS.avatars.villager,
      colors: { bg: "#F8FAFC", text: "#111827", accent: "#111827" },
    };
  }

  return {
    alignment: "unknown",
    avatar: ASSETS.avatars.villager,
    colors: { bg: "#F8FAFC", text: "#111827", accent: "#111827" },
  };
};

const mapPlayerNameToAgentId = (name) => {
  const n = String(name || "");
  const lower = n.toLowerCase();
  if (lower.startsWith("player")) {
    const suffix = lower.slice(6);
    const num = Number(suffix);
    if (Number.isFinite(num) && num >= 1 && num <= 99) {
      return `player_${num}`;
    }
  }
  return null;
};

const applyPlayersInit = (players) => {
  if (!Array.isArray(players) || players.length === 0) return;

  const prev = agents.value.length ? agents.value : DEFAULT_AGENTS;
  const byId = new Map((prev || []).map((a) => [a.id, a]));

  for (const p of players) {
    const agentId = mapPlayerNameToAgentId(p?.name);
    if (!agentId) continue;

    const base = byId.get(agentId) || { id: agentId, name: agentId };
    const seatNum = agentId.startsWith("player_") ? agentId.slice(7) : "";
    const meta = roleMetaFromRole(p?.role);
    byId.set(agentId, {
      ...base,
      id: agentId,
      name: seatNum ? `${Number(seatNum)}号` : base.name,
      role: String(p?.role || "未知"),
      alignment: meta.alignment,
      avatar: meta.avatar,
      colors: meta.colors,
      model: p?.model || base.model,
      alive: p?.alive !== false,
    });
  }

  const nextAgents = Array.from(byId.values()).sort((a, b) => {
    const na = Number(String(a.id).replace("player_", ""));
    const nb = Number(String(b.id).replace("player_", ""));
    return (Number.isFinite(na) ? na : 0) - (Number.isFinite(nb) ? nb : 0);
  });

  agents.value = nextAgents;
};

const markPlayersDead = (deadPlayerNames) => {
  if (!Array.isArray(deadPlayerNames) || deadPlayerNames.length === 0) return;

  agents.value = agents.value.map((agent) => {
    const isDead = deadPlayerNames.some((name) => {
      const deadId = mapPlayerNameToAgentId(name);
      return deadId === agent.id || name === agent.name || name === `${agent.name.replace("号", "")}号`;
    });
    if (isDead) {
      return { ...agent, alive: false };
    }
    return agent;
  });
};

const statusText = computed(() => {
  if (connectionStatus.value === "connected") return "已连接";
  if (connectionStatus.value === "disconnected") return "连接断开";
  return "连接中";
});

const isAuthenticated = computed(() => Boolean(authenticatedUser.value));
const isGameRunning = computed(() => String(gameStatus.value?.status || "").toLowerCase() === "running");

const refreshGameStatus = async () => {
  try {
    const res = await fetch(`${API_URL}/api/game/status`, { method: "GET" });
    if (!res.ok) return;
    const data = await res.json().catch(() => null);
    if (!data) return;
    gameStatus.value = {
      status: data.status || "idle",
      gameId: data.gameId ?? null,
      logPath: data.logPath ?? null,
      experiencePath: data.experiencePath ?? null,
    };
  } catch {
    // ignore
  }
};

const downloadBlob = async (url, fallbackName) => {
  const res = await fetch(url, { method: "GET" });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${text || res.statusText}`.trim());
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const m = cd.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/i);
  const filename = decodeURIComponent((m && (m[1] || m[2])) || fallbackName || "download");

  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
};

const bubbleFor = (idOrName) => {
  if (!idOrName) return null;
  const direct = bubbles.value[idOrName];
  if (direct) return direct;

  const agent = agents.value.find((a) => a.id === idOrName || a.name === idOrName);
  if (!agent) return null;
  return bubbles.value[agent.id] || null;
};

const handleJumpToMessage = (bubble) => {
  if (feedRef.value?.scrollToMessage) {
    feedRef.value.scrollToMessage(bubble);
  }
};

const upsertBubbleFromMessage = (messageOrFeedItem) => {
  if (!messageOrFeedItem) return;

  const isFeedItem =
    typeof messageOrFeedItem === "object" &&
    messageOrFeedItem !== null &&
    Object.prototype.hasOwnProperty.call(messageOrFeedItem, "data") &&
    messageOrFeedItem.data;

  const msg = isFeedItem ? messageOrFeedItem.data : messageOrFeedItem;
  if (!msg || !msg.agentId) return;

  const agent = agents.value.find((a) => a.id === msg.agentId);
  const behaviorText = String(msg.behavior || "").trim();
  const speechTextRaw = String(msg.speech || "").trim();
  const contentTextRaw = String(msg.content || "").trim();
  const hasStructured = Boolean(behaviorText || speechTextRaw);

  const lines = [];
  if (hasStructured) {
    if (behaviorText) {
      lines.push(`(表现) ${extractBubbleText(behaviorText)}`);
    }
    const speechText = speechTextRaw ? speechTextRaw : contentTextRaw;
    if (String(speechText || "").trim()) {
      lines.push(`(发言) ${extractBubbleText(speechText)}`);
    }
  }

  const bubble = {
    agentId: msg.agentId,
    agentName: msg.agentName || msg.agent || agent?.name,
    text: hasStructured ? lines.join("\n") : extractBubbleText(speechTextRaw || contentTextRaw),
    timestamp: msg.timestamp || Date.now(),
    ts: msg.timestamp || Date.now(),
    id: msg.id,
    feedItemId: msg.id,
  };

  bubbles.value = {
    ...bubbles.value,
    [msg.agentId]: bubble,
  };

  const timers = bubbleTimersRef.value;
  if (timers[msg.agentId]) {
    clearTimeout(timers[msg.agentId]);
  }
  timers[msg.agentId] = setTimeout(() => {
    if (!bubbles.value[msg.agentId]) return;
    const next = { ...bubbles.value };
    delete next[msg.agentId];
    bubbles.value = next;
    delete timers[msg.agentId];
  }, BUBBLE_LIFETIME_MS);
};

const handleAgentTyping = (evt) => {
  if (!evt || !evt.agentId) return;

  const agent = agents.value.find((a) => a.id === evt.agentId);
  const bubble = {
    agentId: evt.agentId,
    agentName: evt.agentName || agent?.name,
    text: "",
    isTyping: true,
    category: evt.category,
    categoryDisplay: evt.categoryDisplay,
    timestamp: evt.timestamp || Date.now(),
    ts: evt.timestamp || Date.now(),
  };

  bubbles.value = {
    ...bubbles.value,
    [evt.agentId]: bubble,
  };

  const timers = bubbleTimersRef.value;
  if (timers[evt.agentId]) {
    clearTimeout(timers[evt.agentId]);
  }
  // Typing indication should have a longer timeout to prevent premature disappearance if LLM is slow
  timers[evt.agentId] = setTimeout(() => {
    const currentBubble = bubbles.value[evt.agentId];
    if (currentBubble && currentBubble.isTyping) {
      const next = { ...bubbles.value };
      delete next[evt.agentId];
      bubbles.value = next;
    }
    delete timers[evt.agentId];
  }, TYPING_LIFETIME_MS);
};

const startGame = async () => {
  if (startingGame.value) return;
  startingGame.value = true;
  try {
    addSystemMessage("正在请求后端开始游戏…");
    const res = await fetch(`${API_URL}/api/game/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "admin" }),
    });
    if (!res.ok) {
      const text = await res.text();
      addSystemMessage(`开始游戏失败: ${res.status} ${text || res.statusText}`);
      return;
    }
    const data = await res.json().catch(() => ({}));
    addSystemMessage(`已开始游戏 (gameId=${data?.gameId || ""})`);
    refreshGameStatus();
  } catch (e) {
    addSystemMessage(`开始游戏失败: ${String(e?.message || e)}`);
  } finally {
    startingGame.value = false;
  }
};

const stopGame = async () => {
  if (stoppingGame.value) return;
  stoppingGame.value = true;
  try {
    addSystemMessage("正在请求后端终止游戏…");
    const res = await fetch(`${API_URL}/api/game/stop`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    if (!res.ok) {
      const text = await res.text();
      addSystemMessage(`终止游戏失败: ${res.status} ${text || res.statusText}`);
      return;
    }
    const data = await res.json().catch(() => ({}));
    addSystemMessage(data?.message || "已请求终止游戏");
    refreshGameStatus();
  } catch (e) {
    addSystemMessage(`终止游戏失败: ${String(e?.message || e)}`);
  } finally {
    stoppingGame.value = false;
  }
};

const exportLog = async () => {
  if (exportingLog.value) return;
  exportingLog.value = true;
  try {
    await downloadBlob(`${API_URL}/api/exports/log`, "game.log");
    addSystemMessage("已导出最新游戏日志");
  } catch (e) {
    addSystemMessage(`导出日志失败: ${String(e?.message || e)}`);
  } finally {
    exportingLog.value = false;
  }
};

const exportExperience = async () => {
  if (exportingExperience.value) return;
  exportingExperience.value = true;
  try {
    await downloadBlob(`${API_URL}/api/exports/experience`, "experience.json");
    addSystemMessage("已导出最新经验文件");
  } catch (e) {
    addSystemMessage(`导出经验失败: ${String(e?.message || e)}`);
  } finally {
    exportingExperience.value = false;
  }
};

let statusTimer = null;

const clearBubbleTimers = () => {
  const timers = bubbleTimersRef.value || {};
  Object.keys(timers).forEach((k) => clearTimeout(timers[k]));
  bubbleTimersRef.value = {};
};

const resetRuntimeState = () => {
  connectionStatus.value = "connecting";
  phaseText.value = "准备中";
  startingGame.value = false;
  stoppingGame.value = false;
  exportingLog.value = false;
  exportingExperience.value = false;
  gameStatus.value = { status: "idle", gameId: null, logPath: null, experiencePath: null };
  agents.value = [...DEFAULT_AGENTS];
  bubbles.value = {};
  clearBubbleTimers();
  resetFeed();
};

const teardownRealtime = () => {
  if (statusTimer) {
    clearInterval(statusTimer);
    statusTimer = null;
  }

  clearBubbleTimers();

  if (clientRef.value) {
    try {
      clientRef.value.disconnect();
    } catch {
      // ignore
    }
  }
  clientRef.value = null;
};

const initializeRealtime = () => {
  teardownRealtime();
  refreshGameStatus();
  statusTimer = setInterval(refreshGameStatus, 1500);

  const onEvent = (evt) => {
    if (!evt || !evt.type) return;

    if (evt.type === "system") {
      const content = String(evt.content || "");
      if (content.toLowerCase().includes("connected")) {
        connectionStatus.value = "connected";
      }
      if (content.toLowerCase().includes("try to connect")) {
        connectionStatus.value = "disconnected";
      }

      const category = String(evt.category || "");
      if ((category.includes("死亡") || content.includes("被淘汰")) && Array.isArray(evt.players)) {
        markPlayersDead(evt.players);
      }
    }

    if (evt.type === "system" && Array.isArray(evt.players)) {
      const category = String(evt.category || "");
      if (!category.includes("死亡")) {
        applyPlayersInit(evt.players);
      }
    }

    if (evt.type === "day_start") {
      phaseText.value = "白天";
    }
    if (evt.type === "night_start") {
      phaseText.value = "夜晚";
    }

    if (evt.type === "historical" && Array.isArray(evt.events)) {
      const playersInit = evt.events
        .filter((e) => e && e.type === "system" && Array.isArray(e.players) && !String(e.category || "").includes("死亡"))
        .map((e) => e.players)
        .pop();
      if (playersInit) {
        applyPlayersInit(playersInit);
      }

      const deathEvents = evt.events.filter(
        (e) => e && e.type === "system" && String(e.category || "").includes("死亡") && Array.isArray(e.players)
      );
      for (const deathEvt of deathEvents) {
        markPlayersDead(deathEvt.players);
      }

      processHistoricalFeed(evt.events);
      return;
    }

    processFeedEvent(evt);
    if (evt.type === "agent_message" || evt.type === "conference_message") {
      upsertBubbleFromMessage(evt);
    }
    if (evt.type === "agent_typing") {
      handleAgentTyping(evt);
    }
  };

  const client = new ReadOnlyClient(onEvent);
  clientRef.value = client;

  addSystemMessage("等待游戏开始…");
  client.connect();
};

const handleLogin = async () => {
  const username = String(loginForm.value.username || "").trim();
  const password = String(loginForm.value.password || "");

  if (!username || !password) {
    loginError.value = "请输入用户名和密码。";
    return;
  }

  if (VALID_USERS[username] !== password) {
    loginError.value = "用户名或密码错误，请检查后重试。";
    return;
  }

  isLoggingIn.value = true;
  loginError.value = "";

  try {
    await new Promise((resolve) => setTimeout(resolve, 240));
    authenticatedUser.value = username;
    window.localStorage.setItem(AUTH_STORAGE_KEY, username);
    loginForm.value.password = "";
  } finally {
    isLoggingIn.value = false;
  }
};

const handleLogout = () => {
  window.localStorage.removeItem(AUTH_STORAGE_KEY);
  authenticatedUser.value = "";
  loginError.value = "";
  loginForm.value = {
    username: "",
    password: "",
  };
};

watch(
  isAuthenticated,
  (authed) => {
    if (authed) {
      resetRuntimeState();
      initializeRealtime();
      return;
    }

    teardownRealtime();
    resetRuntimeState();
  },
  { immediate: true }
);

onBeforeUnmount(() => {
  teardownRealtime();
  resetRuntimeState();
});
</script>

<style scoped>
.app--game {
  background:
    radial-gradient(circle at top, rgba(129, 140, 248, 0.14), transparent 35%),
    linear-gradient(180deg, #060b1c 0%, #0b1532 100%);
}

.game-layout {
  flex: 1;
  min-height: 0;
  display: flex;
}

.game-layout__room {
  width: 60%;
  min-width: 0;
  border-right: 1px solid rgba(148, 163, 184, 0.18);
}

.game-layout__feed {
  width: 40%;
  min-width: 360px;
  background: rgba(7, 12, 30, 0.7);
}

.login-screen {
  position: relative;
  min-height: 100vh;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 32px 20px;
  background:
    linear-gradient(rgba(4, 10, 29, 0.4), rgba(2, 6, 23, 0.82)),
    url("/theme/login-castle.png") center center / cover no-repeat;
}

.login-screen::before {
  content: "";
  position: absolute;
  inset: 0;
  background:
    radial-gradient(circle at 50% 18%, rgba(96, 165, 250, 0.22), transparent 32%),
    radial-gradient(circle at 50% 100%, rgba(30, 41, 59, 0.65), transparent 55%);
}

.login-screen__veil {
  position: absolute;
  inset: 0;
  background:
    linear-gradient(180deg, rgba(15, 23, 42, 0.15) 0%, rgba(2, 6, 23, 0.78) 100%);
  -webkit-backdrop-filter: blur(4px);
  backdrop-filter: blur(4px);
}

.login-screen__spark {
  position: absolute;
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: rgba(191, 219, 254, 0.85);
  box-shadow: 0 0 18px rgba(125, 211, 252, 0.75);
  animation: floatSpark linear infinite;
  z-index: 1;
}

.login-panel {
  position: relative;
  z-index: 2;
  width: min(520px, 100%);
  padding: 44px 36px 32px;
  border-radius: 28px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background:
    linear-gradient(180deg, rgba(15, 23, 42, 0.78), rgba(3, 7, 18, 0.92));
  box-shadow:
    0 24px 80px rgba(2, 6, 23, 0.55),
    inset 0 1px 0 rgba(255, 255, 255, 0.08);
  color: #e2e8f0;
}

.login-panel__crest {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 8px 14px;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.16);
  border: 1px solid rgba(125, 211, 252, 0.3);
  color: #bfdbfe;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.18em;
  text-transform: uppercase;
}

.login-panel__title {
  margin: 20px 0 10px;
  font-size: clamp(40px, 8vw, 62px);
  line-height: 1.05;
  font-weight: 900;
  text-align: center;
  color: #eff6ff;
  text-shadow: 0 0 24px rgba(96, 165, 250, 0.28);
}

.login-panel__subtitle {
  margin: 0 0 28px;
  text-align: center;
  font-size: 16px;
  line-height: 1.6;
  color: #cbd5e1;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.login-form__field {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.login-form__field span {
  font-size: 13px;
  font-weight: 700;
  color: #dbeafe;
  letter-spacing: 0.04em;
}

.login-form__field input {
  width: 100%;
  padding: 15px 16px;
  border-radius: 16px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: rgba(15, 23, 42, 0.8);
  color: #f8fafc;
  font-size: 15px;
  outline: none;
  transition: border-color 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}

.login-form__field input::placeholder {
  color: #94a3b8;
}

.login-form__field input:focus {
  border-color: rgba(96, 165, 250, 0.9);
  box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.18);
  transform: translateY(-1px);
}

.login-form__error {
  margin: -4px 0 0;
  color: #fca5a5;
  font-size: 13px;
  line-height: 1.5;
}

.login-form__submit {
  margin-top: 4px;
  padding: 16px 18px;
  border: none;
  border-radius: 18px;
  background: linear-gradient(135deg, #2563eb, #4338ca);
  color: #eff6ff;
  font-size: 16px;
  font-weight: 800;
  letter-spacing: 0.08em;
  cursor: pointer;
  box-shadow: 0 16px 30px rgba(37, 99, 235, 0.28);
  transition: transform 0.2s ease, box-shadow 0.2s ease, opacity 0.2s ease;
}

.login-form__submit:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 22px 36px rgba(37, 99, 235, 0.32);
}

.login-form__submit:disabled {
  opacity: 0.72;
  cursor: wait;
}

.login-panel__tips {
  margin-top: 20px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  color: #93c5fd;
  font-size: 12px;
}

.login-panel__tips span {
  padding: 6px 10px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.56);
  border: 1px solid rgba(96, 165, 250, 0.18);
}

@keyframes floatSpark {
  0%, 100% {
    transform: translateY(0) scale(0.9);
    opacity: 0.35;
  }

  50% {
    transform: translateY(-16px) scale(1.15);
    opacity: 1;
  }
}

@media (max-width: 980px) {
  .game-layout {
    flex-direction: column;
  }

  .game-layout__room,
  .game-layout__feed {
    width: 100%;
  }

  .game-layout__room {
    min-height: 62vh;
    border-right: 0;
    border-bottom: 1px solid rgba(148, 163, 184, 0.18);
  }

  .game-layout__feed {
    min-width: 0;
    min-height: 38vh;
  }
}

@media (max-width: 640px) {
  .login-panel {
    padding: 34px 20px 24px;
    border-radius: 22px;
  }

  .login-panel__subtitle {
    font-size: 14px;
  }
}
</style>
