# 金源智枢 · 多 Agent 协同的财富管理系统

面向投顾、客服、风控、运营、客户经理等多角色的一体化财富管理 AI 中台。系统让 AI 只做「理解、推荐、解释、辅助决策」，而权限、风控、适当性、确认与执行始终由确定性业务系统负责，把模型限制在可解释、可审计、可验证的辅助范围内。

- **多 Agent 编排**：Supervisor 统一编排 + 客服 / 投顾 / 风控 / 数据分析 / 业务操作五类角色 Agent；LangGraph 状态机、条件分支、迭代与工具调用预算、受控 ReAct。
- **安全边界**：能力白名单前置过滤、按角色权限与授权客户范围（Subject Scope）限制 Agent 与工具调用；模型无法越权读取他人数据或直接提交交易。
- **RAG + NL2SQL**：职责分离 —— RAG 负责解释层（公司信息 / 流程 / FAQ），业务事实直接查 PostgreSQL，冲突以业务表为准；dense/BM25 混合检索 + Reranker + 引用溯源。
- **记忆系统**：短期（Redis）/ 长期（PostgreSQL）记忆、偏好抽取与指代消解、跨会话共享语义层（conversation_summary）按 agent_type 召回。
- **数据飞轮**：线上 bad case（含高置信意图误判 `intent_misroute`）自动回流 → 人工标注 → 导出进黄金评测集 → 修复，形成闭环。
- **意图识别**：确定性规则规划器为权威；可选的 LLM 影子/兜底仅用于**非交易**意图，交易类永远走规则。

## 技术栈

FastAPI · SQLAlchemy Async · PostgreSQL + pgvector · Redis Streams + Outbox · LangChain / LangGraph · Qwen（DashScope）· Vue 3 + Vite · Docker Compose · GitHub Actions CI/CD · Pytest

## 快速开始（Docker）

前置：Docker、`.env`（参考 `.env.example`），以及可用的 Qwen/DashScope API Key（RAG 向量与答案生成需要）。

```bash
# 拉起 PostgreSQL / Redis / API / 前端 / 各 Worker
docker compose -f docker-compose.p0.yml up -d --build

# 健康检查
curl http://127.0.0.1:8010/api/v1/health
```

- 默认端口：Web `80`，API `8010`。
- 演示账号（`DEMO_ACCOUNTS_ENABLED=true` 时）：`customer_demo` / `Demo@2026Customer`。
- 本地测试：`python -m pytest tests/ -q`；前端构建：`cd frontend && npm run build`。

## 文档

- [`docs/API.md`](docs/API.md) — HTTP API 约定与主要接口
- [`docs/DATABASE_TABLE_REFERENCE.md`](docs/DATABASE_TABLE_REFERENCE.md) — 数据表结构与字段说明
- [`docs/ERROR_CODES.md`](docs/ERROR_CODES.md) — 统一错误码
- [`docker-compose.p0.yml`](docker-compose.p0.yml) — 生产 P0 编排（服务、端口、健康检查、意图兜底/飞轮采集开关）

## CI/CD

推 `main` 触发 GitHub Actions（`.github/workflows/ci-cd.yml`）：`Test & Build`（pytest + 前端构建）→ `Deploy to Aliyun`（SSH 部署到服务器，docker compose build + up，健康检查带重试）。部署相关 Secrets 见工作流文件头注释。

## 目录

```text
app/            后端（api / services / repositories / models / schemas / agents / infrastructure）
workers/        异步 Worker（outbox / 记忆 / 飞轮 / 知识 / 授权 / 通知 / AML）
migrations/     Alembic 迁移
tests/          Pytest 单元与契约测试
frontend/       Vue 3 前端
scripts/        运维 / 种子 / E2E 脚本
```

## License

[LICENSE](LICENSE)
