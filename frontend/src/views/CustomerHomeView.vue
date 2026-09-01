<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { profileApi } from '../api/profile'
import { tradingApi } from '../api/trading'

const loading=ref(true)
const summary=ref<any>(null)
const recommendations=ref<any>(null)
const account=ref<any>(null)
const error=ref('')

function money(value:any){return Number(value||0).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})}

onMounted(async()=>{
  try{
    const [summaryResponse,recommendationResponse,accountResponse]=await Promise.all([
      profileApi.summary(),
      profileApi.recommendations(),
      tradingApi.account(),
    ])
    summary.value=summaryResponse.data.data
    recommendations.value=recommendationResponse.data.data
    account.value=accountResponse.data.data
  }catch(e:any){
    error.value=e.response?.data?.detail?.message||e.message||'客户资料读取失败'
  }finally{loading.value=false}
})
</script>

<template>
  <section>
    <div class="section-heading">
      <div>
        <span class="eyebrow">CUSTOMER CONSOLE</span>
        <h2>我的财富概览</h2>
        <p>查看客户事实、资产快照与确定性产品适当性结果。</p>
      </div>
      <span class="pill">客户前台</span>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="loading" class="empty">正在读取你的资料…</div>

    <template v-else>
      <div class="metric-grid">
        <div class="metric-card"><span>账户可用余额</span><strong>¥{{ money(account?.available_balance) }}</strong></div>
        <div class="metric-card"><span>总资产</span><strong>¥{{ money(summary?.latest_asset?.total_asset) }}</strong></div>
        <div class="metric-card"><span>可投资资产</span><strong>¥{{ money(recommendations?.context?.investable_asset ?? summary?.latest_asset?.investable_asset) }}</strong></div>
        <div class="metric-card"><span>持仓数量</span><strong>{{ summary?.holdings?.length ?? 0 }}</strong></div>
      </div>

      <div class="profile-panel">
        <span class="eyebrow">PROFILE</span>
        <h3>{{ summary?.profile?.investment_goal || '尚未填写投资目标' }}</h3>
        <p>投资期限：{{ summary?.profile?.investment_horizon || '未设置' }} · 流动性偏好：{{ summary?.profile?.liquidity_preference || '未设置' }}</p>
      </div>

      <div class="section-heading compact">
        <h3>我的持仓</h3><span class="pill">事实数据</span>
      </div>
      <div v-if="!summary?.holdings?.length" class="empty">暂无持仓数据</div>
      <article v-for="holding in summary?.holdings || []" :key="holding.id" class="result">
        <div class="result-meta"><span>{{ holding.product_name || `产品 ${holding.product_id}` }}</span><span>持有 {{ holding.holding_days }} 天</span></div>
        <p>市值：¥{{ money(holding.market_value) }} · 盈亏：¥{{ money(holding.profit_loss) }}</p>
      </article>

      <div class="section-heading compact">
        <div><h3>产品适配中心</h3><p class="muted">规则仅供产品筛选和解释，不会创建订单或执行交易。</p></div>
        <span class="pill">确定性规则</span>
      </div>

      <section class="suitability-context" data-testid="customer-suitability-context">
        <span>当前风险等级 <b>{{ recommendations?.context?.risk_level || 'C1' }}</b></span>
        <span>风险测评状态 <b>{{ recommendations?.context?.risk_assessment_status || 'unknown' }}</b></span>
        <span>可投资资产 <b>¥{{ money(recommendations?.context?.investable_asset) }}</b></span>
      </section>

      <div v-if="recommendations?.warnings?.length" class="suitability-warnings" data-testid="customer-suitability-warnings">
        <p v-for="warning in recommendations.warnings" :key="warning.code">{{ warning.message }}</p>
      </div>

      <div v-if="!recommendations?.matches?.length && !recommendations?.excluded?.length" class="empty">当前没有可评估的在售产品。</div>
      <div class="recommend-grid">
        <article v-for="item in recommendations?.matches || []" :key="item.product_id" class="recommend-card matched" data-testid="customer-suitability-matched">
          <strong>✓ {{ item.product_name }}</strong>
          <div class="recommend-meta"><span>{{ item.product_type }}</span><span>风险 {{ item.product_risk_level }}</span><span>最低 ¥{{ money(item.minimum_investable_asset) }}</span><span>{{ item.liquidity }}</span></div>
          <p v-for="reason in item.reasons" :key="reason">{{ reason }}</p>
          <small v-if="item.suitability_rule">规则：{{ item.suitability_rule }}</small>
        </article>
        <article v-for="item in recommendations?.excluded || []" :key="item.product_id" class="recommend-card excluded" data-testid="customer-suitability-excluded">
          <strong>× {{ item.product_name }}</strong>
          <div class="recommend-meta"><span>{{ item.product_type }}</span><span>风险 {{ item.product_risk_level }}</span><span>最低 ¥{{ money(item.minimum_investable_asset) }}</span><span>{{ item.liquidity }}</span></div>
          <p v-for="reason in item.reasons" :key="reason">{{ reason }}</p>
          <small v-if="item.suitability_rule">规则：{{ item.suitability_rule }}</small>
        </article>
      </div>

      <div class="section-heading compact"><h3>快捷操作</h3></div>
      <div class="action-row"><RouterLink class="button" to="/orders">查看我的订单与交易</RouterLink><RouterLink class="text-button" to="/products">浏览产品</RouterLink></div>
    </template>
  </section>
</template>

<style scoped>
.suitability-context{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 12px}.suitability-context span,.recommend-meta span{border:1px solid var(--line,#d9e2ef);border-radius:999px;padding:5px 9px;background:var(--surface-soft,#f7f9fc);font-size:12px}.suitability-warnings{margin:0 0 14px;padding:10px 14px;border-left:4px solid #d98b1c;background:#fff8e9;color:#76551d}.suitability-warnings p{margin:0}.recommend-meta{display:flex;flex-wrap:wrap;gap:6px;margin:9px 0}.recommend-card small{display:block;margin-top:8px;color:var(--muted,#6b7280)}
</style>
