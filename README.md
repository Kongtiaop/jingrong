# Wealth Manager P0

P0 工程基线：FastAPI + Streamlit + PostgreSQL/pgvector + Redis Streams + Qwen。

## 当前状态

- P0 文档依据：`docs/P0-P5_IMPLEMENTATION_PLAN.md` v2.0
- PostgreSQL 是主库，使用 SQLAlchemy Async ORM
- 向量存储使用 PostgreSQL pgvector，Milvus 不在第一版依赖中
- 队列使用 Redis Streams + Consumer Group
- Prefect 不使用
- Qwen / DashScope 是统一模型入口

## 启动（当前开发阶段）

后端：

```powershell
uvicorn app.main:app --reload
```

前端：

```powershell
streamlit run frontend/streamlit_app.py
```

Worker：

```powershell
python -m workers.runner
```

## 说明

当前只实现 P0 基线，不实现业务 Agent、RAG 导入、交易、风控和记忆逻辑。
