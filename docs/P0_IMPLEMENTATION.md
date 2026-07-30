# P0 implementation notes

> Updated: 2026-07-25

## Implemented

- FastAPI application entry and `/api/v1/health`
- Streamlit P0 landing page
- PostgreSQL Async ORM baseline
- `vector` extension check and baseline schema creation
- `async_task` and `outbox_event` models
- Redis Streams wrapper with Consumer Group, publish, consume and ACK
- Minimal task API: `POST /api/v1/tasks`, `GET /api/v1/tasks/{task_id}`
- Qwen configuration provider and embedding dimension guard
- Alembic async migration baseline
- P0 environment and dependency templates
- Worker runner that consumes and ACKs baseline task messages

## Reused from C:/pg_prefect

- PostgreSQL/pgvector direction
- SQLAlchemy Async ORM style
- async engine/session configuration approach
- document pipeline will be reused in P1, not copied into P0

## Not implemented yet

- Docker Compose for this project
- Real Qwen API smoke call
- PostgreSQL/Redis integration test against running services
- Outbox publisher loop
- Worker task handlers
- RAG ingestion
- business tables
- Agent/Tool/Memory/Risk modules

## Verification commands

```powershell
python -m py_compile app\main.py app\core\settings.py app\services\task_service.py workers\runner.py scripts\check_p0.py
```

With PostgreSQL and Redis available:

```powershell
uvicorn app.main:app --reload
python -m workers.runner
```

## 2026-07-28 P0 依赖环境验证

`langchain_env` 已确认可用于本项目 P0：

```text
Python 3.12.13
FastAPI 0.140.0
SQLAlchemy 2.0.51
asyncpg 0.31.0
redis 7.4.1
pgvector 0.5.0
PyJWT 2.13.0
LangChain 1.3.14
LangGraph 1.2.9
```

P0 入口和 Redis Worker 已按当前环境版本完成兼容调整。新增：

```text
scripts/check_dependencies.py
```

后续在 `langchain_env` 中执行：

```powershell
python scripts/check_dependencies.py
```
