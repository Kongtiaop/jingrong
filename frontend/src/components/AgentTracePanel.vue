<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ trace?: any }>()

// 被安全护栏拦下的三类：越出计划范围 / 重复调用 / 不在只读白名单。
// 与后端 customer_model_tools.py 的 ToolRound.error 取值保持一致。
const BLOCKED_ERRORS = new Set(['tool_out_of_plan_scope', 'duplicate_call_skipped', 'tool_not_allowed'])
const BLOCKED_LABEL: Record<string, string> = {
  tool_out_of_plan_scope: '不在本次计划范围内',
  duplicate_call_skipped: '与已执行调用参数重复',
  tool_not_allowed: '不在只读工具白名单内',
}

const rounds = computed<any[]>(() => {
  const list = props.trace?.rounds
  return Array.isArray(list) ? list : []
})

const decisions = computed<any[]>(() => {
  const list = props.trace?.decisions
  return Array.isArray(list) ? list : []
})

function isBlocked(round: any): boolean {
  return BLOCKED_ERRORS.has(String(round?.error || ''))
}

function statusOf(round: any): 'ok' | 'blocked' | 'failed' {
  if (round?.success) return 'ok'
  return isBlocked(round) ? 'blocked' : 'failed'
}

function statusText(round: any): string {
  const s = statusOf(round)
  if (s === 'ok') return '成功'
  if (s === 'blocked') return '被拦截'
  return '失败'
}

function blockedReason(round: any): string {
  return BLOCKED_LABEL[String(round?.error || '')] || String(round?.error || '未知原因')
}

const blockedCount = computed(() => rounds.value.filter(isBlocked).length)

// 总耗时 = 工具执行耗时 + 该轮决策 LLM 调用耗时。
// 两者分开记是有意的：工具慢说明下游查询慢，LLM 慢说明模型侧慢，排障时结论完全不同。
const totalDurationMs = computed(() =>
  Math.round(rounds.value.reduce((sum, item) =>
    sum + (Number(item?.duration_ms) || 0) + (Number(item?.llm_duration_ms) || 0), 0)))

const totalTokens = computed(() =>
  rounds.value.reduce((sum, item) =>
    sum + (Number(item?.prompt_tokens) || 0) + (Number(item?.completion_tokens) || 0), 0))

// token 缺失时显示"—"而不是 0：确定性路径（规则补齐）不经过 LLM，
// 记成 0 会让成本统计看起来"免费"，与事实不符。
const tokensText = computed(() => (totalTokens.value > 0 ? String(totalTokens.value) : '—'))

const layerText = computed(() => {
  const layer = props.trace?.layer
  return layer === null || layer === undefined ? '' : `第 ${layer} 层 · `
})

function fmtArgs(args: any): string {
  if (!args || typeof args !== 'object') return String(args ?? '—')
  try {
    const text = JSON.stringify(args)
    return text.length > 200 ? `${text.slice(0, 200)}…` : text
  } catch {
    return '—'
  }
}

function fmtDuration(round: any): string {
  const parts: string[] = []
  const llm = Number(round?.llm_duration_ms)
  const tool = Number(round?.duration_ms)
  if (llm > 0) parts.push(`决策 ${Math.round(llm)}ms`)
  if (tool > 0) parts.push(`执行 ${Math.round(tool)}ms`)
  return parts.length ? parts.join(' · ') : '—'
}

function fmtTokens(round: any): string {
  const prompt = Number(round?.prompt_tokens) || 0
  const completion = Number(round?.completion_tokens) || 0
  if (!prompt && !completion) return '—'
  return `${prompt}↑ ${completion}↓`
}
</script>

<template>
  <details v-if="rounds.length" class="agent-trace" data-testid="agent-trace-panel">
    <summary class="agent-trace-summary" data-testid="agent-trace-toggle">
      <span>查看推理过程</span>
      <small>
        {{ layerText }}{{ rounds.length }} 次工具调用<template v-if="blockedCount"> · <b class="blocked">{{ blockedCount }} 次被拦截</b></template>
        · {{ totalDurationMs }}ms · {{ tokensText }} tokens
      </small>
    </summary>

    <div class="agent-trace-body">
      <article
        v-for="round in rounds"
        :key="`${round.round_index}-${round.tool}`"
        class="trace-round"
        :class="statusOf(round)"
        :data-testid="`agent-trace-round-${statusOf(round)}`"
      >
        <header>
          <span class="trace-index">{{ round.round_index }}</span>
          <b class="trace-tool">{{ round.tool }}</b>
          <span class="trace-status">{{ statusText(round) }}</span>
          <small class="trace-metrics">{{ fmtDuration(round) }} · {{ fmtTokens(round) }}</small>
        </header>

        <p v-if="round.reasoning" class="trace-reasoning" data-testid="agent-trace-reasoning">
          <label>调用理由</label>{{ round.reasoning }}
        </p>
        <p class="trace-args"><label>入参</label>{{ fmtArgs(round.arguments) }}</p>
        <p v-if="round.output_preview" class="trace-output" data-testid="agent-trace-output">
          <label>输出</label>{{ round.output_preview }}
        </p>
        <p v-if="statusOf(round) === 'blocked'" class="trace-blocked" data-testid="agent-trace-blocked">
          <label>拦截原因</label>{{ blockedReason(round) }}
        </p>
        <p v-else-if="round.error" class="trace-error"><label>错误</label>{{ round.error }}</p>
      </article>

      <p v-if="trace?.stopped_reason" class="trace-footer">
        停止原因：<b>{{ trace.stopped_reason }}</b>
        <template v-if="decisions.length"> · 模型决策 {{ decisions.length }} 次</template>
      </p>
    </div>
  </details>
</template>

<style scoped>
.agent-trace { margin-top: 8px; border-top: 1px dashed rgba(0, 0, 0, 0.12); padding-top: 8px; }
.agent-trace-summary { cursor: pointer; display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.agent-trace-summary span { font-weight: 500; }
.agent-trace-summary small { color: #6b7280; font-size: 12px; }
.agent-trace-summary small .blocked { color: #b45309; }
.agent-trace-body { margin-top: 10px; display: flex; flex-direction: column; gap: 10px; }
.trace-round { border-left: 3px solid #d1d5db; padding: 6px 0 6px 10px; font-size: 13px; }
.trace-round.ok { border-left-color: #16a34a; }
.trace-round.blocked { border-left-color: #d97706; }
.trace-round.failed { border-left-color: #dc2626; }
.trace-round header { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.trace-index { width: 18px; height: 18px; border-radius: 50%; background: #f3f4f6; color: #4b5563; font-size: 11px; display: inline-flex; align-items: center; justify-content: center; }
.trace-tool { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.trace-status { font-size: 11px; padding: 1px 6px; border-radius: 999px; background: #f3f4f6; color: #4b5563; }
.trace-round.ok .trace-status { background: #dcfce7; color: #15803d; }
.trace-round.blocked .trace-status { background: #fef3c7; color: #b45309; }
.trace-round.failed .trace-status { background: #fee2e2; color: #b91c1c; }
.trace-metrics { color: #9ca3af; font-size: 11px; }
.trace-round p { margin: 3px 0 0; color: #374151; line-height: 1.6; word-break: break-word; }
.trace-round label { display: inline-block; min-width: 56px; color: #9ca3af; font-size: 11px; }
.trace-reasoning { color: #4b5563 !important; font-style: italic; }
.trace-blocked { color: #b45309 !important; }
.trace-error { color: #b91c1c !important; }
.trace-footer { margin: 4px 0 0; font-size: 12px; color: #6b7280; }
</style>
