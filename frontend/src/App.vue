<script setup>
import { onMounted, onBeforeUnmount } from 'vue'
import { useTaskStore } from './stores/task'
import { useWorkflowStore } from './stores/workflow'
import HealthBar from './components/HealthBar.vue'
import UploadPanel from './components/UploadPanel.vue'
import TaskCard from './components/TaskCard.vue'
import ProviderManageModal from './components/ProviderManageModal.vue'
import StrategyModal from './components/StrategyModal.vue'
import WorkflowCanvas from './components/WorkflowCanvas.vue'

const store = useTaskStore()
const wfStore = useWorkflowStore()

onMounted(() => {
  store.checkHealth()
  store.loadConfig()
  store.loadProviders()
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
        <button
          type="button"
          class="rounded-lg border border-emerald-500/50 px-3 py-1.5 text-xs font-medium text-emerald-300 transition hover:border-emerald-400 hover:bg-emerald-500/10"
          @click="store.openManage()"
        >
          模型管理
        </button>
        <button
          type="button"
          class="rounded-lg border border-emerald-500/50 px-3 py-1.5 text-xs font-medium text-emerald-300 transition hover:border-emerald-400 hover:bg-emerald-500/10"
          @click="store.openStrategy()"
        >
          策略设置
        </button>
        <button
          type="button"
          class="rounded-lg border border-blue-500/50 px-3 py-1.5 text-xs font-medium text-blue-300 transition hover:border-blue-400 hover:bg-blue-500/10"
          @click="wfStore.open()"
        >
          工作流
        </button>
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

    <ProviderManageModal :open="store.managing" @close="store.closeManage()" />
    <StrategyModal :open="store.strategizing" @close="store.closeStrategy()" />
    <WorkflowCanvas />
  </div>
</template>
