import { API_URL } from "../config/constants";

export async function fetchGameStatus() {
  const res = await fetch(`${API_URL}/api/game/status`, { method: "GET" });
  if (!res.ok) throw new Error(`获取状态失败: ${res.status}`);
  return res.json();
}

export async function startUserGame() {
  const res = await fetch(`${API_URL}/api/game/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ mode: "user" }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `开始游戏失败: ${res.status}`);
  }
  return res.json();
}

export async function stopGame() {
  const res = await fetch(`${API_URL}/api/game/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `终止游戏失败: ${res.status}`);
  }
  return res.json();
}

export async function exportLog() {
  return fetch(`${API_URL}/api/exports/log`, { method: "GET" });
}

export async function exportExperience() {
  return fetch(`${API_URL}/api/exports/experience`, { method: "GET" });
}

export async function fetchPendingHumanAction() {
  const res = await fetch(`${API_URL}/api/human/pending`, { method: "GET" });
  if (!res.ok) throw new Error(`获取待处理操作失败: ${res.status}`);
  return res.json();
}

export async function submitHumanAction(payload) {
  const res = await fetch(`${API_URL}/api/human/submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(text || `提交操作失败: ${res.status}`);
  }
  return res.json();
}
