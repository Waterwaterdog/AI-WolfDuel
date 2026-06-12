<template>
  <div class="bgm-panel">
    <button
      type="button"
      class="bgm-panel__mute"
      :title="muted ? '取消静音' : '静音'"
      :aria-label="muted ? '取消静音' : '静音'"
      @click="onToggleMute"
    >
      <svg v-if="!muted" viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M5 10.5a1 1 0 0 1 1-1h3.2l4.1-3.4A1 1 0 0 1 15 6.9v10.2a1 1 0 0 1-1.7.8l-4.1-3.4H6a1 1 0 0 1-1-1v-3Z"
          fill="currentColor"
        />
        <path
          d="M18.2 8.3a1 1 0 0 1 1.4 0 5.25 5.25 0 0 1 0 7.4 1 1 0 0 1-1.4-1.4 3.25 3.25 0 0 0 0-4.6 1 1 0 0 1 0-1.4Z"
          fill="currentColor"
        />
        <path
          d="M16.1 6.2a1 1 0 0 1 1.4 0 8.25 8.25 0 0 1 0 11.6 1 1 0 1 1-1.4-1.4 6.25 6.25 0 0 0 0-8.8 1 1 0 0 1 0-1.4Z"
          fill="currentColor"
        />
      </svg>
      <svg v-else viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M5 10.5a1 1 0 0 1 1-1h3.2l4.1-3.4A1 1 0 0 1 15 6.9v10.2a1 1 0 0 1-1.7.8l-4.1-3.4H6a1 1 0 0 1-1-1v-3Z"
          fill="currentColor"
        />
        <path
          d="M17.1 9.5a1 1 0 0 1 1.4 0l3 3a1 1 0 1 1-1.4 1.4l-3-3a1 1 0 0 1 0-1.4Z"
          fill="currentColor"
        />
        <path
          d="M20.1 9.5a1 1 0 0 1 0 1.4l-3 3a1 1 0 1 1-1.4-1.4l3-3a1 1 0 0 1 1.4 0Z"
          fill="currentColor"
        />
      </svg>
    </button>
  </div>
</template>

<script setup>
defineProps({
  muted: { type: Boolean, default: false },
  onToggleMute: { type: Function, default: null },
});
</script>

<style scoped>
.bgm-panel {
  position: fixed;
  right: 18px;
  bottom: 18px;
  z-index: 60;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 8px;
  border-radius: 999px;
  background: rgba(2, 6, 23, 0.82);
  border: 1px solid rgba(148, 163, 184, 0.22);
  box-shadow: 0 18px 45px rgba(15, 23, 42, 0.3);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}

.bgm-panel__mute {
  border: 0;
  border-radius: 999px;
  width: 42px;
  height: 42px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #2563eb, #4338ca);
  color: #eff6ff;
  cursor: pointer;
  transition: transform 0.2s ease, box-shadow 0.2s ease, opacity 0.2s ease;
}

.bgm-panel__mute:hover {
  transform: translateY(-1px);
  box-shadow: 0 10px 20px rgba(37, 99, 235, 0.28);
}

.bgm-panel__mute svg {
  width: 20px;
  height: 20px;
}

@media (max-width: 640px) {
  .bgm-panel {
    right: 12px;
    bottom: 12px;
  }
}
</style>
