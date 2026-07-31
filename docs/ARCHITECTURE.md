# 可替换基础设施与 Agent 接口

## 当前目标

业务层不直接绑定 Agent 实验实现、向量数据库或未来数据库厂商。

```text
API / Service
    ↓
Port（Agent / VectorStore / Repository）
    ↓
Adapter（本地 Agent / pgvector / Milvus / PostgreSQL / MySQL）
```

## Agent 接口

- `app/ports/agent.py`：`Agent`、`AgentContext`。
- `app/agents/orchestrator.py`：统一注册和调用 Agent。
- 实验 Agent 必须适配为 `run(message, context) -> AgentResult`，不得直接修改订单、资产或风险等级。

## 向量存储接口

- `app/ports/vector_store.py`：`VectorStore`、`VectorDocument`、`VectorHit`。
- `app/infrastructure/vector_store/pgvector.py`：当前适配器骨架。
- Milvus 适配器可在不修改业务服务的情况下新增。

## 配置

```env
DATABASE_BACKEND=postgresql
VECTOR_STORE_BACKEND=pgvector
AGENT_BACKEND=local
```

当前只是完成端口和配置抽象；MySQL/Milvus 还没有声明为可直接切换，必须完成适配器、迁移脚本和回归测试后再启用。

## Repository Port 第一阶段

新增：

- `app/ports/repositories.py`：CustomerRepository、ProductRepository、RiskAssessmentRepository。
- `app/repositories/profile.py`：当前 PostgreSQL/SQLAlchemy 实现。

风险测评服务和产品适配服务已开始通过 Repository 读取风险测评和产品数据。后续可增加 MySQL 实现而不修改业务服务。


## 交易与资产 Repository 抽象

新增：

- `app/ports/trading.py`：Account、Order、Holding、Asset Repository Port。
- `app/repositories/trading.py`：当前 SQLAlchemy 实现。

`AssetSummaryService` 已迁移账户、持仓汇总和资产快照读取到 Repository。交易服务后续继续迁移订单、账户和持仓写入，以保持事务边界。


## 外部基础设施连通性

- 已确认本机 3306 TCP 端口可连接，MySQL 驱动 `aiomysql` 尚未安装，因此未进行写入迁移。
- 已确认 `milvus-standalone` 容器运行，Milvus 19530 可连接，当前集合列表为空。
- 新增 `MilvusVectorStore` 适配器骨架和配置：`MILVUS_URI`、`MILVUS_COLLECTION`。
- 当前不切换生产链路；Milvus 集合 Schema、索引、数据迁移脚本完成后再启用。


## MySQL 迁移测试环境

已创建隔离容器：

```text
容器：wealth-mysql-migration
镜像：mysql:8.0
宿主端口：3307
数据库：wealth_manager
用户：wealth
密码：wealth_dev
root 密码：wealth_root_migration
```

该容器只用于迁移适配测试，不影响当前 PostgreSQL 主库。


## MySQL 最小迁移探针

隔离 MySQL 8.0 容器已完成连接、建表、写入、重复写入和读取验证。当前仅验证类型和连接兼容，不代表完整业务表已迁移。


## MySQL 核心 Schema 探针

已在隔离 MySQL 容器建立并验证最小核心表：

```text
users
customer_profile
product
customer_risk_assessment
account
```

已验证 UUID 字符串主键、唯一约束、DECIMAL 金额、JSON 答案和客户画像关联查询。

## Milvus 向量探针

已连接现有 `milvus-standalone`，创建临时集合 `wealth_documents_probe`，验证：

- VARCHAR 主键
- FLOAT_VECTOR
- COSINE + AUTOINDEX
- insert/flush/load/search/delete

业务集合尚未创建，临时探针集合只用于能力验证。


## 正式 Milvus 知识库迁移

已创建正式集合：

```text
wealth_documents
```

字段包括：

- `id`：知识块 ID
- `vector`：1024 维 embedding
- `text`：知识块文本
- `document_id`
- `version_id`
- `title_path`
- `content_hash`

索引：

```text
AUTOINDEX + COSINE
```

当前从 PostgreSQL active 知识块迁移 12 条，原 PostgreSQL/pgvector 数据保留。
