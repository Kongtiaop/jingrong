# P0 本地基础设施

本项目使用独立 Compose，不影响当前 `simple_rag` 的容器。

## 与 simple_rag 的端口隔离

当前 simple_rag 已占用：

```text
Redis 6379
Milvus 19530
Attu 8001
```

本项目 P0 使用：

```text
PostgreSQL 5433 -> 容器 5432
Redis 6380 -> 容器 6379
```

因此两套环境可以同时运行。

## 启动

复制环境模板：

```powershell
Copy-Item .env.p0.example .env.p0
```

启动：

```powershell
docker compose --env-file .env.p0 -f docker-compose.p0.yml up -d
```

查看状态：

```powershell
docker compose --env-file .env.p0 -f docker-compose.p0.yml ps
```

查看日志：

```powershell
docker compose --env-file .env.p0 -f docker-compose.p0.yml logs -f postgres redis
```

停止：

```powershell
docker compose --env-file .env.p0 -f docker-compose.p0.yml down
```

停止并删除 P0 数据卷（会删除本项目 PostgreSQL/Redis 数据）：

```powershell
docker compose --env-file .env.p0 -f docker-compose.p0.yml down -v
```

## 本地应用连接配置

宿主机启动 FastAPI 时，`.env` 可以配置：

```env
DATABASE_URL=postgresql+asyncpg://wealth:wealth_dev@127.0.0.1:5433/wealth_manager
REDIS_URL=redis://127.0.0.1:6380/0
```

如果未来把 FastAPI 和 Worker 也放进同一个 Compose，容器内连接应改为：

```env
DATABASE_URL=postgresql+asyncpg://wealth:wealth_dev@postgres:5432/wealth_manager
REDIS_URL=redis://redis:6379/0
```

## 注意

- `docker-compose.p0.yml` 不包含 Milvus，因为当前项目第一版使用 PostgreSQL + pgvector。
- 不要停止或删除 `rag-redis`，它属于 simple_rag。
- 不要使用 `docker compose down -v` 操作 simple_rag 的 Compose 环境。
