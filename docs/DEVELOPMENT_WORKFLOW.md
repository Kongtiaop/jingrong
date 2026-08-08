# 标准开发门禁（当前无 CI）

> 当前仓库仅配置 Gitee Git remote，没有发现 GitHub Actions、Gitee Go、Jenkins 或其他自动流水线。`git push`/`git pull` 只负责代码同步，不等于 CI。

## 当前标准流程

修改涉及后端、Repository/UoW、数据库迁移或 MySQL 双读时，在提交和推送前执行：

```powershell
python -m py_compile app\main.py app\api\profile.py app\api\admin.py app\services\trading_service.py scripts\migrate_core_to_mysql.py scripts\validate_mysql_schema.py
conda run -n langchain_env python -m scripts.reset_mysql_dev_state --yes
conda run -n langchain_env python -m scripts.migrate_core_to_mysql
conda run -n langchain_env python -m scripts.test_mysql_migration_gate
conda run -n langchain_env python -m scripts.compare_customer_summary_dual_read
conda run -n langchain_env python -m scripts.test_trading_flow
conda run -n langchain_env python -m tests.test_http_smoke --base-url http://127.0.0.1:8010/api/v1 --accounts DEMO_ACCOUNTS.txt
```

## 通过标准

- 命令全部返回成功；
- MySQL migration 输出 `schema_validation=PASS`；
- migration gate 的 `pass=true`；
- 客户摘要双读 `differences=[]`；
- 交易闭环 PASS；
- HTTP Smoke 输出 `status=PASS`。

## 数据安全

- `reset_mysql_dev_state --yes` 只清理本地 MySQL 隔离库，不清理 PostgreSQL、Redis、Milvus；
- 不要在生产环境执行 reset 脚本；
- profile/admin HTTP Smoke 使用的测试记录必须执行对应 cleanup 脚本；
- 如果双读失败，先记录差异，不要直接手工修改 PostgreSQL 正式业务数据。

## Gitee 操作

标准门禁全部通过后再执行：

```powershell
git status --short
git add .
git commit -m "describe change"
git push origin main
```

未来如果接入 Gitee Go/Jenkins 等流水线，流水线应直接复用上述命令，而不是维护另一套测试逻辑。

## AML 日批 Worker

AML Scheduler 是独立容器，默认不随基础设施启动。完成 `.env` 中 UTC 时间和窗口天数配置后，显式启动：

```powershell
docker compose -f docker-compose.p0.yml --profile aml up -d aml-scheduler
docker compose -f docker-compose.p0.yml logs -f aml-scheduler
```

停止该 Worker 不影响 PostgreSQL、Redis、Milvus：

```powershell
docker compose -f docker-compose.p0.yml --profile aml stop aml-scheduler
```

Scheduler 成功处理同一个 UTC 分钟仅一次；扫描失败时会在下一轮轮询尝试。服务层使用 `batch_key` 复用同一扫描窗口，因此容器重启或人工补跑不会重复创建 AML 批次。

## Docker 一键启动：API 与 AML Worker 分离

当前本地 P0 环境使用两个独立镜像：`Dockerfile.api` 只运行 FastAPI，`Dockerfile.aml-scheduler` 只运行 AML 定时 Worker；旧通用 `Dockerfile` 已删除。

```powershell
# 启动 PostgreSQL、Redis 和 API
docker compose -f docker-compose.p0.yml up -d --build

# 验证 API（容器首次启动会执行 schema/种子初始化）
Invoke-WebRequest http://127.0.0.1:8010/api/v1/health

# 仅在需要定时 AML 时额外启动 Worker
docker compose -f docker-compose.p0.yml --profile aml up -d --build aml-scheduler
```

全量环境使用 `docker compose up -d --build`，其中包含 Milvus；MySQL 仍需通过 `--profile migration` 显式启用。


## 知识库上传自动 Pipeline

管理员/产品管理员可在后台“知识库管理”上传 UTF-8 TXT、MD 或 DOCX（单文件最大 20MB）。上传 API 仅保存文件与文档元数据，并写入 `knowledge.document_created` Outbox 事件；随后 `outbox-worker` 发布到 Redis Stream，`knowledge-worker` 负责解析、切分、调用 Embedding 并写入 PostgreSQL `knowledge_chunk.embedding`（pgvector）。

Compose 默认会启动 API、Outbox Worker 和 Knowledge Worker：

```powershell
docker compose -f docker-compose.p0.yml up -d --build
docker compose -f docker-compose.p0.yml logs -f outbox-worker knowledge-worker
```

如果文档长期保持“处理中”，优先检查上述两个 Worker；如果状态为“处理失败”，在后台文档列表查看失败原因。`uploads/knowledge` 是容器内临时文件路径，生产部署应挂载持久卷或替换为对象存储。

## Docker 中文路径构建注意事项

当前 Windows/Docker Desktop 对本项目中文路径可能触发 BuildKit gRPC header 错误。日常构建前使用 ASCII 盘符映射：

```powershell
subst W: "C:\Users\tx360\Desktop\金融 - 副本"
Set-Location W:\
docker compose -f docker-compose.p0.yml up -d --build api outbox-worker knowledge-worker
```

不要使用 `docker compose down -v`，以免删除本地 PostgreSQL、Redis、Milvus 数据卷。

## 前端本地启动与 Network Error 排查

前端使用 Vite 开发服务器的 `/api` 代理访问 Docker API。日常开发必须从 `frontend` 目录启动：

```powershell
Set-Location frontend
npm run dev -- --host 127.0.0.1
```

浏览器访问：

```text
http://127.0.0.1:5173
```

Vite 会将 `/api/*` 代理到 `http://127.0.0.1:8010`。不要直接双击构建产物或通过未配置代理的静态服务打开前端，否则相对地址 `/api/v1/...` 无法到达后端，页面会显示 `Network Error`，登录、`/auth/me`、知识库上传和用户数据读取会同时失败。

快速检查：

```powershell
Invoke-WebRequest http://127.0.0.1:5173/
Invoke-WebRequest http://127.0.0.1:5173/api/v1/health
```

两者都应返回 HTTP 200；若 `8010` 健康正常但 `5173` 拒绝连接，应先启动 Vite，而不是重建 Docker API。