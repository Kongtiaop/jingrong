# 智能财富管家

基于 **FastAPI + Vue 3/Vite + PostgreSQL/pgvector + Redis Streams + Qwen** 的智能财富管理演示系统。项目当前已覆盖客户画像、企业认证、风险测评、产品适配、模拟交易、资产汇总、审计和基础 Agent/存储抽象。

> 当前定位：可运行、可演示、可扩展的 Mock/MVP 系统。所有交易均为模拟业务，不连接真实金融交易系统。

## 当前能力

- 用户注册、登录、JWT/刷新令牌和角色权限。
- 客户画像：个人资料编辑、客户类型、客户层级、资产和持仓。
- 企业客户认证：客户提交、超级管理员审核、审计记录。
- 客户风险测评：C1-C5、C1 provisional 默认等级、测评有效期。
- 产品适配：风险等级、客户类型、最低资产/金额的确定性校验。
- 模拟交易：订单创建、确认、审核、执行、交易记录和持仓更新。
- 资产汇总：账户现金 + 有效持仓市值，交易后生成派生资产快照。
- 后台管理：客户、产品、订单、企业认证、审计、回收站。
- 存储抽象：Repository Port、VectorStore Port、Agent Contract。
- MySQL 8 和 Milvus 已完成隔离环境探针及首批知识向量迁移验证；主链路仍使用 PostgreSQL/pgvector。

## 技术结构

```text
frontend (Vue 3 + Vite)
        │ HTTP / JSON
        ▼
FastAPI /api/v1
        ├── API 路由
        ├── Service 业务规则
        ├── Repository Port
        │     └── SQLAlchemy PostgreSQL Adapter
        ├── Agent Contract / Orchestrator
        └── VectorStore Port
              ├── pgvector
              └── Milvus Adapter（迁移验证中）
```

主要目录：

```text
app/api/             API 路由
app/services/        业务服务与确定性规则
app/models/          SQLAlchemy 模型
app/schemas/         Pydantic 请求/响应模型
app/ports/           Agent、Repository、VectorStore 接口
app/repositories/    当前数据库适配实现
app/infrastructure/  Qwen、Redis、向量存储适配器
frontend/src/        Vue 前端
scripts/             Seed、迁移和联调脚本
docs/                计划、测试、问题和架构文档
```

## 启动

### 1. 准备环境

推荐使用 Conda 环境：

```powershell
conda activate langchain_env
```

基础设施：

```powershell
docker compose up -d
```

### 2. 配置环境变量

复制并修改：

```powershell
Copy-Item .env.example .env
```

开发环境常用配置：

```env
DATABASE_URL=postgresql+asyncpg://wealth:wealth_dev@127.0.0.1:5433/wealth_manager
REDIS_URL=redis://127.0.0.1:6380/0
VECTOR_STORE_BACKEND=pgvector
MILVUS_URI=http://127.0.0.1:19530
```

### 3. 启动后端

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
```

健康检查：

```text
http://127.0.0.1:8010/api/v1/health
```

FastAPI 在线文档：

```text
http://127.0.0.1:8010/docs
```

### 4. 启动前端

```powershell
cd frontend
npm install
npm run dev
```

生产构建：

```powershell
npm run build
```

### 5. 启动 Worker（如需）

```powershell
python -m workers.runner
```

## 测试

项目的 HTTP 回归脚本使用独立脚本模式，不依赖 pytest 收集：

```powershell
conda run -n langchain_env python tests/test_http_smoke.py --base-url http://127.0.0.1:8010/api/v1
conda run -n langchain_env python tests/test_http_faults.py --base-url http://127.0.0.1:8010/api/v1
conda run -n langchain_env python tests/test_http_concurrency.py --base-url http://127.0.0.1:8010/api/v1
```

当前已验证的主要结果：

- HTTP Smoke：PASS。
- Faults：12/12 PASS。
- 并发与订单幂等：通过。
- C1-C5 风险适配和企业客户类型适配：通过。
- MySQL 核心 Schema 探针：通过。
- Milvus 向量写入/检索/删除和知识块迁移：通过。

## 演示账号

账号清单见：

```text
DEMO_ACCOUNTS.txt
```

该文件只用于本地开发、联调和答辩演示，生产环境禁止保存明文密码。

## 数据库与向量存储迁移边界

当前主链路：

```text
PostgreSQL + pgvector
```

已经完成抽象和验证，但切换到 MySQL/Milvus 仍需完成完整 Adapter、全量数据迁移、双读对比和回滚方案。不能仅修改配置后直接用于生产。详细说明见：

- `docs/ARCHITECTURE.md`
- `docs/PLAN.md`
- `docs/TESTS.md`
- `docs/API.md`

## 重要边界

- LLM/Agent 负责理解、解释和建议；金额、权限、客户类型、风险等级、适当性和交易状态由后端确定性逻辑决定。
- 所有交易均为 Mock，不产生真实资金变化。
- 客户类型、客户层级和正式风险等级不能由客户直接伪造修改。
- Milvus/MySQL 迁移环境与当前 PostgreSQL 主系统隔离。


## 一键启动

基础服务：

```powershell
docker compose up -d
```

默认启动：

- PostgreSQL/pgvector：5433
- Redis：6380
- Milvus gRPC：19530
- Milvus HTTP：9091

同时启动隔离 MySQL 迁移库：

```powershell
docker compose --profile migration up -d
```

注意：Docker Compose 负责启动基础设施；FastAPI、Vue 前端和 Worker 仍需分别启动，避免开发热重载进程被容器接管。
