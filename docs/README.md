# 项目文档入口

本目录包含智能财富管家项目的架构、业务、接口、测试和实施记录。组员第一次加入项目时，先阅读：

1. `docs/TEAM_ONBOARDING.md`：团队脚手架、目录地图、启动与分工。
2. `docs/DEVELOPMENT_WORKFLOW.md`：提交前本地门禁和 Docker/前端启动方式。
3. `docs/P0-P5_IMPLEMENTATION_PLAN.md`：当前有效的阶段目标和技术约束。
4. `docs/DECISIONS.md`：已冻结的技术决策和弃用方案。
5. `docs/ARCHITECTURE_OVERVIEW.md`：系统模块、数据流和边界说明。
6. `docs/API.md`：后端 HTTP API 约定。
7. `docs/TESTS.md`：测试命令、验收结果和未覆盖范围。
8. `docs/PLAN.md`、`docs/ISSUES.md`：持续维护的计划与问题记录。

## 文档优先级

发生冲突时按以下顺序判断：

```text
P0-P5_IMPLEMENTATION_PLAN.md
        ↓
DECISIONS.md
        ↓
ARCHITECTURE_DATABASE_DRAFT.md / ARCHITECTURE_OVERVIEW.md
        ↓
PLAN.md 中的历史记录
```

业务制度、牌照、金额、期限和保存要求不能只依赖总结文档，必须回到当前有效的原始制度文件。

## 文档同步说明

当前仓库的 `.gitignore` 历史上将部分详细设计文档视为本地恢复文件。脚手架本身只依赖 `TEAM_ONBOARDING.md`、`DEVELOPMENT_WORKFLOW.md` 和根目录 README；如果团队需要在新机器 clone 后完整获得架构/计划文档，应在确认敏感信息和账号信息已脱敏后，将对应文档纳入版本控制。

每次实现完成后至少更新：

- `docs/PLAN.md`：完成内容、阶段状态、下一步和关键文件；
- `docs/TESTS.md`：执行命令、环境、实际结果和未覆盖范围；
- `docs/ISSUES.md`：问题、结论、处理方式和验证结果；
- `docs/DECISIONS.md`：形成新架构决策时同步更新。
