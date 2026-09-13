<script setup>
import { onMounted, onBeforeUnmount } from 'vue'
import { useTaskStore } from './stores/task'
import HealthBar from './components/HealthBar.vue'
import UploadPanel from './components/UploadPanel.vue'
import TaskCard from './components/TaskCard.vue'

const store = useTaskStore()

onMounted(() => {
  store.checkHealth()
  // 页面卸载时停止轮询
  window.addEventListener('pagehide', onUnload)
})

onBeforeUnmount(() => {
  window.removeEventListener('pagehide', onUnload)
  store._stopPolling()
})

function onUnload() {
  store._stopPolling()
}
</script>

<template>
  <div class="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-emerald-950/40">
    <header class="sticky top-0 z-10 border-b border-slate-800/60 bg-slate-950/80 backdrop-blur">
      <div class="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
        <div class="flex items-center gap-2">
          <span class="text-2xl">🎾</span>
          <h1 class="text-lg font-bold tracking-tight">
            TennisClip <span class="text-emerald-400">AI</span>
          </h1>
        </div>
        <HealthBar :health="store.health" />
      </div>
    </header>

    <main class="mx-auto grid max-w-5xl gap-6 px-4 py-8 md:grid-cols-2">
      <UploadPanel
        :loading="store.loading"
        :has-task="!!store.current"
        @submit="(file, level) => store.submit(file, level)"
      />
      <div>
        <TaskCard
          :task="store.current"
          :error="store.error"
          @clear="store.clear()"
        />
      </div>
    </main>

    <footer class="mx-auto max-w-5xl px-4 py-6 text-center text-xs text-slate-500">
      基于阶跃星辰 Step 3.7 Flash · OpenAI 兼容协议 · 全链路自动化高光剪辑与技术分析
    </footer>
  </div>
</template>
