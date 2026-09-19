<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { useTaskStore } from '../stores/task'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['close'])

const store = useTaskStore()

// 管线阶段固定顺序（与后端 DEFAULT_STAGES 一致）
const STAGE_DEFS = [
  { key: 'preprocess', label: '预处理' },
  { key: 'highlight', label: '高光识别' },
  { key: 'edit', label: '剪辑' },
  { key: 'report', label: '报告' },
]
const LEVEL_DEFS = [
  { value: 'beginner', label: '入门' },
  { value: 'intermediate', label: '进阶' },
  { value: 'professional', label: '专业' },
  { value: 'all', label: '所有高光回合' },
]

const stages = reactive({ preprocess: true, highlight: true, edit: true, report: true })
const analysisMode = ref('frame')
const level = ref('intermediate')

// 打开时从全局配置同步当前值
watch(
  () => props.open,
  (v) => {
    if (v) {
      for (const s of STAGE_DEFS) {
        stages[s.key] = store.pipelineStages ? store.pipelineStages.includes(s.key) : true
      }
      analysisMode.value = store.analysisMode || 'frame'
      level.value = store.defaultLevel || 'intermediate'
    }
  },
)

// 依赖约束：非预处理阶段依赖预处理；剪辑/报告依赖高光识别
function stageDisabled(key) {
  if (key === 'preprocess') return false
  if (!stages.preprocess) return true
  if ((key === 'edit' || key === 'report') && !stages.highlight) return true
  return false
}

function toggleStage(key) {
  if (stageDisabled(key)) return
  stages[key] = !stages[key]
}

// 上游阶段关闭时，级联关闭下游
watch(
  () => stages.preprocess,
  (on) => {
    if (!on) {
      stages.highlight = false
      stages.edit = false
      stages.report = false
    }
  },
)
watch(
  () => stages.highlight,
  (on) => {
    if (!on) {
      stages.edit = false
      stages.report = false
    }
  },
)

const enabledStages = computed(() => STAGE_DEFS.map((s) => s.key).filter((k) => stages[k]))

const canSave = computed(() => enabledStages.value.length > 0)

async function save() {
  if (!canSave.value || store.strategySaving) return
  await store.saveStrategy({
    stages: enabledStages.value,
    analysisMode: analysisMode.value,
    level: level.value,
  })
  if (!store.strategyError) emit('close')
}

function close() {
  emit('close')
}
</script>

<template>
  <div
    v-if="open"
    class="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur-sm"
    @click.self="close"
  >
    <div
      class="flex max-h-[88vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/80 shadow-2xl backdrop-blur"
    >
      <!-- 头部 -->
      <div class="flex items-center justify-between border-b border-slate-800 px-5 py-4">
        <h3 class="flex items-center gap-2 text-base font-semibold text-slate-100">
          <span class="text-lg">⚙️</span> 策略调整
        </h3>
        <button
          type="button"
          class="rounded-lg px-2 py-1 text-slate-400 transition hover:bg-slate-800 hover:text-slate-100"
          @click="close"
        >
          ✕
        </button>
      </div>

      <!-- 内容 -->
      <div class="flex-1 space-y-6 overflow-y-auto px-5 py-5">
        <p class="text-xs text-slate-500">
          以下为全局策略，保存后影响之后所有任务。阶段固定顺序执行，可启用/停用；分析模式与层级可独立调整。
        </p>

        <!-- 管线阶段 -->
        <section>
          <h4 class="mb-3 text-sm font-medium text-slate-200">管线阶段（固定顺序）</h4>
          <div class="space-y-2">
            <div
              v-for="s in STAGE_DEFS"
              :key="s.key"
              class="flex items-center justify-between rounded-xl border px-4 py-3 transition"
              :class="stageDisabled(s.key)
                ? 'border-slate-800 bg-slate-800/20 opacity-50'
                : 'border-slate-700 bg-slate-800/40'"
            >
              <div class="min-w-0">
                <span class="text-sm text-slate-100">{{ s.label }}</span>
                <p
                  v-if="stageDisabled(s.key) && (s.key === 'edit' || s.key === 'report') && stages.highlight === false"
                  class="mt-0.5 text-[11px] text-slate-500"
                >
                  依赖「高光识别」
                </p>
                <p
                  v-else-if="stageDisabled(s.key) && s.key !== 'preprocess'"
                  class="mt-0.5 text-[11px] text-slate-500"
                >
                  依赖「预处理」
                </p>
              </div>
              <button
                type="button"
                role="switch"
                :aria-checked="stages[s.key]"
                :disabled="stageDisabled(s.key)"
                class="relative h-6 w-11 shrink-0 rounded-full transition"
                :class="[
                  stages[s.key] ? 'bg-emerald-500' : 'bg-slate-700',
                  stageDisabled(s.key) ? 'cursor-not-allowed' : 'cursor-pointer hover:opacity-90',
                ]"
                @click="toggleStage(s.key)"
              >
                <span
                  class="absolute top-0.5 left-0.5 h-5 w-5 rounded-full bg-white transition-all"
                  :class="stages[s.key] ? 'translate-x-5' : 'translate-x-0'"
                ></span>
              </button>
            </div>
          </div>
        </section>

        <!-- 分析模式 -->
        <section>
          <h4 class="mb-1 text-sm font-medium text-slate-200">高光识别 · 分析模式</h4>
          <p class="mb-3 text-xs text-slate-500">
            抽帧：将视频抽成图片帧送模型；视频理解：整段视频直送，模型自由定位（推荐网络/时长允许时使用）。
          </p>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <button
              type="button"
              class="rounded-xl border px-4 py-3 text-left transition"
              :class="analysisMode === 'frame'
                ? 'border-emerald-400 bg-emerald-500/10'
                : 'border-slate-700 bg-slate-800/40 hover:border-slate-600'"
              @click="analysisMode = 'frame'"
            >
              <div class="text-sm font-medium text-slate-100">抽帧模式（默认）</div>
              <div class="mt-1 text-[11px] text-slate-400">逐帧图片理解，稳定可控</div>
            </button>
            <button
              type="button"
              class="rounded-xl border px-4 py-3 text-left transition"
              :class="analysisMode === 'video'
                ? 'border-emerald-400 bg-emerald-500/10'
                : 'border-slate-700 bg-slate-800/40 hover:border-slate-600'"
              @click="analysisMode = 'video'"
            >
              <div class="text-sm font-medium text-slate-100">视频理解模式</div>
              <div class="mt-1 text-[11px] text-slate-400">整段视频直送，时序更完整</div>
            </button>
          </div>
        </section>

        <!-- 分析层级 -->
        <section>
          <h4 class="mb-3 text-sm font-medium text-slate-200">分析层级</h4>
          <div class="flex flex-wrap gap-2">
            <button
              v-for="lv in LEVEL_DEFS"
              :key="lv.value"
              type="button"
              class="rounded-lg px-4 py-1.5 text-xs font-medium transition"
              :class="level === lv.value
                ? 'bg-emerald-500 text-slate-950'
                : 'border border-slate-700 bg-slate-800/40 text-slate-300 hover:border-emerald-400 hover:text-emerald-300'"
              @click="level = lv.value"
            >
              {{ lv.label }}
            </button>
          </div>
        </section>
      </div>

      <!-- 底部操作 -->
      <div class="flex items-center justify-between border-t border-slate-800 px-5 py-4">
        <p v-if="store.strategyError" class="text-xs text-red-300">{{ store.strategyError }}</p>
        <span v-else></span>
        <div class="flex gap-2">
          <button
            type="button"
            class="rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-300 transition hover:bg-slate-800"
            @click="close"
          >
            取消
          </button>
          <button
            type="button"
            :disabled="!canSave || store.strategySaving"
            :class="[
              'rounded-lg px-4 py-1.5 text-xs font-semibold transition',
              canSave && !store.strategySaving
                ? 'bg-emerald-500 text-slate-950 hover:bg-emerald-400'
                : 'cursor-not-allowed bg-slate-800 text-slate-500',
            ]"
            @click="save"
          >
            {{ store.strategySaving ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
