<script setup lang="ts">
import { computed } from 'vue'

type AnalysisData = Record<string, any>

const props = defineProps<{ data: AnalysisData }>()
const chart = computed(() => props.data?.chart_spec || {})
const plan = computed(() => props.data?.query_plan || {})
const chartType = computed(() => chart.value.type || 'metric')
const rows = computed(() => {
  const value = props.data?.rows || props.data?.data_rows || props.data?.items || []
  return Array.isArray(value) ? value : []
})
const labels = computed(() => Array.isArray(chart.value.labels)
  ? chart.value.labels
  : rows.value.map((row: any) => row?.label ?? row?.name ?? row?.dimension ?? ''))
const values = computed(() => Array.isArray(chart.value.values)
  ? chart.value.values
  : rows.value.map((row: any) => Number(row?.value ?? row?.metric ?? 0)))
const maxValue = computed(() => Math.max(...values.value.map((value: any) => Number(value) || 0), 1))
const isChart = computed(() => ['bar', 'line', 'pie'].includes(chartType.value))
const displayValue = computed(() => {
  const value = props.data?.value
  return typeof value === 'number' ? value.toLocaleString('zh-CN') : value ?? '-'
})

function barWidth(value: any) {
  return `${Math.max(0, Math.min(100, (Number(value) || 0) / maxValue.value * 100))}%`
}

function formatPlanValue(value: any) {
  return Array.isArray(value) ? value.join('、') : String(value)
}
</script>

<template>
  <section class="analysis-result-card" data-testid="analysis-result-card">
    <header class="analysis-result-header">
      <div>
        <span class="analysis-eyebrow">受控数据分析</span>
        <h4>{{ chart.title || data.title || data.metric_code || '分析结果' }}</h4>
      </div>
      <span class="analysis-badge">{{ chartType }}</span>
    </header>

    <div v-if="chartType === 'metric' || data.value !== undefined" class="analysis-metric">
      <strong>{{ displayValue }}</strong>
      <span>{{ data.unit || chart.unit || '' }}</span>
    </div>

    <p v-if="data.explanation" class="analysis-explanation">{{ data.explanation }}</p>

    <div v-if="data.clarification || data.error || data.denied_reason" class="analysis-warning" data-testid="analysis-clarification">
      <strong>需要补充或无法执行</strong>
      <p>{{ data.clarification || data.error || data.denied_reason }}</p>
      <small v-if="data.allowed_range">允许范围：{{ data.allowed_range }}</small>
    </div>

    <div v-if="chartType === 'table' && rows.length" class="analysis-table-wrap">
      <table>
        <thead>
          <tr><th v-for="(_, key) in rows[0]" :key="key">{{ key }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in rows" :key="index">
            <td v-for="(value, key) in row" :key="key">{{ value }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="isChart && labels.length" class="analysis-chart" :data-chart-type="chartType">
      <div v-for="(label, index) in labels" :key="String(label) + index" class="analysis-chart-row">
        <span class="analysis-chart-label">{{ label || ('项目 ' + (index + 1)) }}</span>
        <div class="analysis-chart-track"><i :style="{ width: barWidth(values[index]) }"></i></div>
        <b>{{ values[index] ?? 0 }}</b>
      </div>
    </div>

    <details v-if="Object.keys(plan).length" class="analysis-plan">
      <summary>查看查询计划与安全边界</summary>
      <dl>
        <template v-for="(value, key) in plan" :key="key">
          <dt>{{ key }}</dt><dd>{{ formatPlanValue(value) }}</dd>
        </template>
      </dl>
    </details>
  </section>
</template>

<style scoped>
.analysis-result-card{margin-top:10px;padding:12px;border:1px solid #cbd5e1;border-radius:12px;background:#f8fafc;color:#0f172a}
.analysis-result-header{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
.analysis-eyebrow{font-size:10px;color:#2563eb;text-transform:uppercase;letter-spacing:.08em}
.analysis-result-header h4{margin:3px 0 0;font-size:14px}.analysis-badge{font-size:10px;border-radius:999px;padding:3px 7px;background:#dbeafe;color:#1d4ed8}
.analysis-metric{display:flex;gap:6px;align-items:baseline;margin:12px 0 6px}.analysis-metric strong{font-size:27px}.analysis-metric span{color:#64748b}
.analysis-explanation{font-size:12px;line-height:1.55;color:#475569}.analysis-warning{margin-top:9px;padding:8px;border-left:3px solid #f59e0b;background:#fffbeb;color:#92400e;font-size:12px}
.analysis-warning p{margin:4px 0}.analysis-table-wrap{overflow:auto;margin-top:10px}.analysis-table-wrap table{width:100%;border-collapse:collapse;font-size:11px}
.analysis-table-wrap th,.analysis-table-wrap td{padding:6px 7px;text-align:left;border-bottom:1px solid #e2e8f0;white-space:nowrap}.analysis-table-wrap th{color:#475569;background:#f1f5f9}
.analysis-chart{display:grid;gap:7px;margin-top:12px}.analysis-chart-row{display:grid;grid-template-columns:minmax(64px,1fr) 2fr auto;gap:7px;align-items:center;font-size:11px}
.analysis-chart-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.analysis-chart-track{height:8px;border-radius:99px;background:#e2e8f0;overflow:hidden}.analysis-chart-track i{display:block;height:100%;border-radius:inherit;background:linear-gradient(90deg,#2563eb,#38bdf8)}
.analysis-chart-row b{min-width:34px;text-align:right;font-size:11px}.analysis-plan{margin-top:10px;border-top:1px solid #e2e8f0;padding-top:8px;font-size:11px}.analysis-plan summary{cursor:pointer;color:#334155}
.analysis-plan dl{display:grid;grid-template-columns:auto 1fr;gap:4px 8px;margin:8px 0 0}.analysis-plan dt{color:#64748b}.analysis-plan dd{margin:0;word-break:break-word;color:#334155}
</style>
