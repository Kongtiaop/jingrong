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
