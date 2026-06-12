import { readonly, ref } from "vue";

const STORAGE_KEYS = {
  volume: "wolfagents_bgm_volume",
  muted: "wolfagents_bgm_muted",
};

const SCENE_TO_TRACK = {
  login: new URL("../../../Music/login.mp3", import.meta.url).href,
  lobby: new URL("../../../Music/lobby.mp3", import.meta.url).href,
  day: new URL("../../../Music/day.mp3", import.meta.url).href,
  night: new URL("../../../Music/night.mp3", import.meta.url).href,
};

const DEFAULT_VOLUME = 0.55;
const FADE_DURATION_MS = 600;
const FADE_STEP_MS = 50;

const scene = ref("login");
const volume = ref(loadInitialVolume());
const muted = ref(loadInitialMuted());
const ready = ref(false);

let currentAudio = null;
let fadeState = null;
let unlockBound = false;

function loadInitialVolume() {
  if (typeof window === "undefined") return DEFAULT_VOLUME;
  const raw = Number(window.localStorage.getItem(STORAGE_KEYS.volume));
  if (!Number.isFinite(raw)) return DEFAULT_VOLUME;
  return clamp(raw, 0, 1);
}

function loadInitialMuted() {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(STORAGE_KEYS.muted) === "true";
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function persistState() {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEYS.volume, String(volume.value));
  window.localStorage.setItem(STORAGE_KEYS.muted, String(muted.value));
}

function getTargetVolume() {
  return muted.value ? 0 : clamp(volume.value, 0, 1);
}

function clearFadeTimer(finalize = false) {
  if (!fadeState) return;
  clearInterval(fadeState.timerId);
  const finalizeTransition = fadeState.finalize;
  fadeState = null;
  if (finalize) finalizeTransition?.();
}

function bindUnlockListeners() {
  if (typeof window === "undefined" || unlockBound) return;
  const resume = () => {
    ensurePlayback();
  };
  window.addEventListener("pointerdown", resume, { passive: true });
  window.addEventListener("keydown", resume, { passive: true });
  unlockBound = true;
}

function createAudio(src) {
  const audio = new Audio(src);
  audio.loop = true;
  audio.preload = "auto";
  audio.volume = 0;
  return audio;
}

function fadeAudio(audio, from, to, durationMs, onDone) {
  clearFadeTimer();

  if (!audio) {
    onDone?.();
    return;
  }

  const steps = Math.max(1, Math.round(durationMs / FADE_STEP_MS));
  let step = 0;
  audio.volume = clamp(from, 0, 1);

  const finalizeTransition = () => {
    audio.volume = clamp(to, 0, 1);
    onDone?.();
  };

  const timerId = setInterval(() => {
    step += 1;
    const progress = step / steps;
    audio.volume = clamp(from + (to - from) * progress, 0, 1);

    if (step >= steps) {
      clearFadeTimer(true);
    }
  }, FADE_STEP_MS);

  fadeState = { timerId, finalize: finalizeTransition };
}

async function tryPlay(audio) {
  if (!audio) return false;
  try {
    await audio.play();
    ready.value = true;
    return true;
  } catch {
    return false;
  }
}

async function switchTrack(nextScene) {
  if (nextScene === "silent") {
    if (!currentAudio) {
      clearFadeTimer(true);
      return;
    }
    const previousAudio = currentAudio;
    currentAudio = null;
    fadeAudio(previousAudio, previousAudio.volume, 0, FADE_DURATION_MS, () => {
      previousAudio.pause();
      previousAudio.currentTime = 0;
      previousAudio.volume = 0;
    });
    return;
  }

  const src = SCENE_TO_TRACK[nextScene] || SCENE_TO_TRACK.lobby;
  if (currentAudio?.src === src) {
    syncVolume(true);
    return;
  }

  const nextAudio = createAudio(src);
  const canPlay = await tryPlay(nextAudio);
  if (!canPlay) return;

  const targetVolume = getTargetVolume();
  const previousAudio = currentAudio;
  currentAudio = nextAudio;

  if (!previousAudio) {
    clearFadeTimer(true);
    fadeAudio(nextAudio, 0, targetVolume, FADE_DURATION_MS);
    return;
  }

  clearFadeTimer(true);
  const steps = Math.max(1, Math.round(FADE_DURATION_MS / FADE_STEP_MS));
  let step = 0;
  const previousStart = previousAudio.volume;

  const finalizeTransition = () => {
    previousAudio.pause();
    previousAudio.currentTime = 0;
    previousAudio.volume = 0;
    nextAudio.volume = targetVolume;
  };

  const timerId = setInterval(() => {
    step += 1;
    const progress = step / steps;
    previousAudio.volume = clamp(previousStart * (1 - progress), 0, 1);
    nextAudio.volume = clamp(targetVolume * progress, 0, 1);

    if (step >= steps) {
      clearFadeTimer(true);
    }
  }, FADE_STEP_MS);

  fadeState = { timerId, finalize: finalizeTransition };
}

function syncVolume(animated = false) {
  const targetVolume = getTargetVolume();
  if (!currentAudio) return;

  if (!animated) {
    clearFadeTimer(true);
    currentAudio.volume = targetVolume;
    return;
  }

  clearFadeTimer(true);
  fadeAudio(currentAudio, currentAudio.volume, targetVolume, 220);
}

async function ensurePlayback() {
  await switchTrack(scene.value);
}

async function setScene(nextScene) {
  const resolvedScene = nextScene === "silent"
    ? "silent"
    : (SCENE_TO_TRACK[nextScene] ? nextScene : "lobby");
  scene.value = resolvedScene;
  await ensurePlayback();
}

function setVolume(nextVolume) {
  volume.value = clamp(Number(nextVolume) || 0, 0, 1);
  persistState();
  syncVolume(true);
}

function toggleMuted() {
  muted.value = !muted.value;
  persistState();
  syncVolume(true);
}

function stopPlayback() {
  clearFadeTimer(true);
  if (!currentAudio) return;
  currentAudio.pause();
  currentAudio.currentTime = 0;
  currentAudio.volume = 0;
  currentAudio = null;
}

export function useBackgroundMusic() {
  bindUnlockListeners();

  return {
    scene: readonly(scene),
    volume: readonly(volume),
    muted: readonly(muted),
    ready: readonly(ready),
    setScene,
    setVolume,
    toggleMuted,
    ensurePlayback,
    stopPlayback,
  };
}
