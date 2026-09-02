# 演示与 E2E 验收 Runbook

本仓库的 CI/CD 已打通：推 `main` → GitHub Actions `Test & Build`（pytest + 前端 build）→ `Deploy to Aliyun`（SSH 到 121.43.113.196，docker compose build + up，健康检查带重试）。**改完代码 push 即自动部署，无需手动上服务器。**

## 1. 自动部署的开关（已通过 docker-compose.p0.yml 默认开启，可在服务器 .env 覆盖）

- `LLM_INTENT_FALLBACK_ENABLED` 默认 `true`：对**非交易**意图（推荐 T2 / 只读 / 转人工）允许高置信 LLM 兜底；交易类仍走规则，LLM 永不创建/提交交易。
- `BAD_CASE_COLLECT` 默认 `1`：让 unknown / 低置信 / **intent_misroute** 落飞轮积累池。
- `LLM_INTENT_SHADOW_ENABLED` 默认 `false`（fallback 开启时本身会落影子审计，无需重复开）。
- 若要关：在服务器 `/opt/wealth/.env` 写 `LLM_INTENT_FALLBACK_ENABLED=false` 或 `BAD_CASE_COLLECT=0`，再 `docker compose -f docker-compose.p0.yml up -d`。

## 2. 一键跑 E2E 验收脚本（在服务器上）

服务器宿主无 python 依赖，统一进 **api 容器**执行（脚本已打进镜像，容器内 API 端口是 8000）：

```bash
cd /opt/wealth
docker compose -f docker-compose.p0.yml exec api \
  env E2E_BASE=http://127.0.0.1:8000/api/v1 \
  python scripts/test_recommendation_precision_e2e.py
```

脚本验收：
1. 普通推荐返回"精准 Top-N"（matches ≤ 5），且每条带 `recommendation_reason`（逐条"为什么"）。
2. 返回带 `preference_note`（总通过数 + 精选了哪些）。
3. **"我能购买什么产品 / 能买哪些"** 被路由到产品推荐（带 matches），而非误路由进交易草稿。
4. 用户"我偏稳健、长期持有"后，后续推荐理由能读到偏好、提到"稳健"。
退出码：0=PASS，1=有字段缺失（会打印出来）。
> 线上已实测通过：`RECOMMENDATION PRECISION E2E PASS`（在 `LLM_INTENT_FALLBACK_ENABLED=true` 下也无回归）。

## 3. 飞轮闭环验证（本轮新增 intent_misroute 盲区补漏）

misroute 会在**规则自信路由交易草稿、而高置信影子 LLM 判只读/推荐**时，落一条 `intent_misroute` 坏样本。
验证：
```bash
cd /opt/wealth
# 观察积累池是否在增长
tail -n 20 logs/bad_cases.jsonl          # 应能看到含 "intent_misroute" 的记录
# bad-case-worker 已把新记录落库（也可在后台标注页/annotation 接口看到）
```

人工标注界面（BadCaseAnnotationView）已把 `intent_misroute` 显示为"高置信误判(规则vs影子分歧)"。
标注 → 导出进黄金评测集 → auto_repair，飞轮即闭环。

> 线上已实测闭环：在 api 容器内强制写一条合成 misroute → `logs/bad_cases.jsonl` 出现
> `{"reasons":["intent_misroute"],"rule_template":"T4_PURCHASE_DRAFT","llm_template":"T2_PRODUCT_RECOMMENDATION"}`
> → `bad_case` 表已落库 `('帮我买点稳妥的理财', ['intent_misroute'])`。采集→写文件→worker 落库全通。

## 4. 手动演示要点（给客户/面试看）

| 步骤 | 输入 | 应看到 |
|---|---|---|
| 1 | 登录 customer_demo / Demo@2026Customer | 进入前台 |
| 2 | "帮我推荐适合我的稳健理财产品" | 推荐 ≤5 条，每条带"契合您稳健风险偏好/满足适当性…"的理由；底部 preference_note 说明总通过数 |
| 3 | "我能购买什么产品？" | 仍是产品推荐（带理由），不是下单/申购草稿 |
| 4 | "我偏稳健、长期持有" → "再推荐一下" | 推荐理由命中"稳健/长期"偏好（记忆生效） |
| 5 | （可在投顾端/后台共享记忆侧看到 conversation_summary 被记忆 worker 提炼并可按 agent_type 召回） | 跨会话上下文可用 |

## 5. 本地回归（改代码后跑）

```bash
# conda 环境已配好
conda run -n langchain_env python -m pytest tests/ -q   # 期望全绿（当前 196 passed）
cd frontend && npm run build                              # 前端构建（CI 也会验证）
```

## 6. 线上验证"非交易 LLM 兜底"增益（fallback=ON 时）

`LLM_INTENT_FALLBACK_ENABLED=true` 的作用：把**正则规则覆盖不到、但其实是推荐意图**的问句，由高置信 LLM 提升为产品推荐，从而减少手工加规则。

在 api 容器内跑探针（打印每条的真实路由，人工判断是否被提升为推荐）：

```bash
docker compose -f docker-compose.p0.yml exec api \
  env E2E_BASE=http://127.0.0.1:8000/api/v1 \
  python scripts/probe_llm_fallback_gain.py
```

若多数打印 `✅推荐(被提升)` → 增益生效；若多为 `⚠️非推荐` → 可能模型未识别或仍被规则拦截，回传输出到会话即可分析。
