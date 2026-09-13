<script setup>
import { computed } from 'vue'

const props = defineProps({
  report: { type: Object, default: null },
})

const report = computed(() => props.report)
const hasContent = computed(() => !!report.value && !!report.value.summary)
</script>

<template>
  <div>
    <h3 class="mb-2 text-sm font-semibold text-slate-100">技术分析报告</h3>

    <p v-if="!hasContent" class="rounded-lg bg-slate-800 p-4 text-center text-xs text-slate-500">
      报告生成中…
    </p>

    <div v-else class="space-y-3">
      <div class="rounded-xl bg-slate-800/60 p-4">
        <p class="text-xs font-semibold uppercase tracking-wide text-emerald-400">总体评价</p>
        <p class="mt-1 text-sm text-slate-200">{{ report.summary }}</p>
        <p v-if="report.level" class="mt-1 text-xs text-slate-500">
          分析层级：{{ report.level }}
        </p>
      </div>

      <div v-if="report.strokes?.length" class="rounded-xl bg-slate-800/60 p-4">
        <p class="text-xs font-semibold uppercase tracking-wide text-amber-400">动作诊断</p>
        <ul class="mt-2 space-y-2">
          <li v-for="(st, i) in report.strokes" :key="i" class="rounded-lg bg-slate-900/50 p-3">
            <div class="flex items-center justify-between">
              <span class="text-sm font-medium text-slate-100">{{ st.action }}</span>
              <SeverityBadge :severity="st.severity" />
            </div>
            <p v-if="st.problem" class="mt-1 text-xs text-red-300/80">问题：{{ st.problem }}</p>
            <p v-if="st.cause" class="mt-0.5 text-xs text-amber-300/80">成因：{{ st.cause }}</p>
            <p v-if="st.suggestion" class="mt-0.5 text-xs text-emerald-300/80">建议：{{ st.suggestion }}</p>
          </li>
        </ul>
      </div>

      <div v-if="report.training_plan?.length" class="rounded-xl bg-slate-800/60 p-4">
        <p class="text-xs font-semibold uppercase tracking-wide text-emerald-400">训练改进方案</p>
        <ul class="mt-2 list-disc pl-5 text-sm text-slate-300">
          <li v-for="(tp, i) in report.training_plan" :key="i" class="leading-relaxed">{{ tp }}</li>
        </ul>
      </div>

      <div class="grid grid-cols-2 gap-3 text-xs">
        <div v-if="report.strengths?.length" class="rounded-lg bg-emerald-500/10 p-3">
          <p class="font-semibold text-emerald-400">优势</p>
          <ul class="mt-1 list-disc pl-4 text-emerald-200/80">
            <li v-for="(s, i) in report.strengths" :key="i">{{ s }}</li>
          </ul>
        </div>
        <div v-if="report.weaknesses?.length" class="rounded-lg bg-red-500/10 p-3">
          <p class="font-semibold text-red-400">待改进</p>
          <ul class="mt-1 list-disc pl-4 text-red-200/80">
            <li v-for="(w, i) in report.weaknesses" :key="i">{{ w }}</li>
          </ul>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import SeverityBadge from './SeverityBadge.vue'
export default { components: { SeverityBadge } }
</script>
