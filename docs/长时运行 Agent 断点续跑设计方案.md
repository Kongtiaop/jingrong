# 长时运行 Agent 断点续跑设计方案

> **文档用途**：技术方案设计、实施计划、代码改造指南
> **适用项目**：智能财富管理平台（Wealth Manager）
> **文档日期**：2026-08-13
> **状态**：设计完成，待实施

## 目录

1. 为什么需要断点续跑
2. 现有能力盘点
3. 总体架构设计
4. Phase 1：Agent 执行快照
5. Phase 2：LangGraph Checkpointer 接入
6. Phase 3：AML 批次完整恢复
7. Phase 4：Webhook 唤醒长时等待
8. 实施优先级与工期估算
9. 代码改动清单
10. 验收标准

## 1. 为什么需要断点续跑

### 1.1 已有长时运行场景

当前系统已存在多个需要长时间运行或等待的场景：





| 场景             | 当前实现                                | 风险                            |
| ---------------- | --------------------------------------- | ------------------------------- |
| AML 日批扫描     | `DailyAmlScanService` 扫描全部客户      | 单客户失败导致全批次重跑        |
| Agent 多意图查询 | `CustomerTaskExecutor` 执行多个子任务   | Worker 崩溃后客户需重新提问     |
| 知识库文档导入   | `KnowledgeIngestionService` + Embedding | 文档更新时全量重建向量          |
| 客服 ReAct 推理  | 多轮 LLM 调用                           | 第 3 轮崩溃后前 2 轮 Token 浪费 |
| 外部 API 等待    | 未来对接银行/支付接口                   | 进程阻塞，无法释放资源          |

### 1.2 设计目标

1. **任务级恢复**：Worker 崩溃后，从断点继续执行，不重跑已完成步骤
2. **会话级恢复**：Agent 多轮推理可中断恢复，不浪费已消耗的 Token
3. **批次级恢复**：批量扫描中单个客户失败不影响其他客户
4. **外部等待恢复**：长时等待不阻塞 Worker，通过 Webhook 唤醒

## 2. 现有能力盘点

### 2.1 已具备的断点基础设施





| 组件                     | 文件位置                          | 已具备能力                                    |
| ------------------------ | --------------------------------- | --------------------------------------------- |
| `AsyncTask`              | `app/models/async_task.py`        | 任务状态、重试次数、失败原因、幂等键          |
| `OutboxEvent`            | `app/models/outbox.py`            | 事件持久化、重试、死信                        |
| `AmlBatchRun`            | `app/models/aml.py`               | 批次幂等、失败客户统计、补跑入口              |
| `WorkOrder` 状态机       | `app/models/workorder.py`         | `pending_customer` 暂停-恢复                  |
| `CustomerServiceSession` | `app/models/customer_service.py`  | `waiting_human` → `human_active` → `paused`   |
| `LangGraph`              | `requirements.txt`                | `MemorySaver` / `AsyncPostgresSaver` 原生支持 |
| Redis 短期记忆           | `app/services/customer_memory.py` | 会话级 `TaskPlan`、`task_results`             |

### 2.2 缺口分析

**问题**：上述能力散落在不同模块，没有统一到 Agent 执行层。

text

```
AmlBatchRun 可以补跑失败客户（✅ 已有）
WorkOrder 可以暂停-恢复（✅ 已有）
AsyncTask 可以重试（✅ 已有）
LangGraph 支持 Checkpointer（✅ 已安装）

但 CustomerTaskExecutor 执行到一半崩溃后无法恢复（❌ 缺失）
DataAnalysisAgent 的批准模板执行无快照（❌ 缺失）
Agent 调用外部 API 无等待恢复机制（❌ 缺失）
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 3. 总体架构设计

### 3.1 三层恢复模型

text

```
┌─────────────────────────────────────────────────────────────────┐
│                    请求层（客户/内部用户）                       │
│                           ↓                                    │
├─────────────────────────────────────────────────────────────────┤
│              会话级恢复（LangGraph Checkpointer）               │
│         多轮 LLM 推理中断后从 Checkpoint 继续                   │
│                           ↓                                    │
├─────────────────────────────────────────────────────────────────┤
│              任务级恢复（Agent Execution Snapshot）             │
│         多意图任务执行中断后从快照恢复                          │
│                           ↓                                    │
├─────────────────────────────────────────────────────────────────┤
│              批次级恢复（AmlBatchRun + 补跑）                  │
│         批量扫描中单个客户失败不影响其他客户                    │
│                           ↓                                    │
├─────────────────────────────────────────────────────────────────┤
│              外部等待恢复（Outbox + Webhook）                  │
│         长时等待不阻塞 Worker，回调后唤醒                      │
└─────────────────────────────────────────────────────────────────┘
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 3.2 各层职责边界





| 层级     | 适用场景                 | 恢复粒度          | 存储位置                              |
| -------- | ------------------------ | ----------------- | ------------------------------------- |
| 会话级   | 客服/投顾 Agent 多轮推理 | 单个 LLM 调用轮次 | PostgreSQL `checkpoint` 表            |
| 任务级   | 多意图任务执行           | 单个子任务        | Redis 快照 + `AsyncTask.payload_json` |
| 批次级   | AML 日批扫描             | 单个客户          | `AmlBatchRun.metadata`                |
| 外部等待 | 银行接口/人工审核        | 单个外部请求      | Redis 快照 + `AsyncTask` 状态         |

## 4. Phase 1：Agent 执行快照

### 4.1 目标

`CustomerTaskExecutor` 执行多意图任务时，如果 Worker 崩溃，重启后能从断点继续，而不是让客户重新提问。

### 4.2 数据模型

#### 4.2.1 快照 Schema

python

```
# app/schemas/agent_snapshot.py

from datetime import datetime
from typing import Dict, List, Optional, Any
from pydantic import BaseModel
from app.schemas.customer_service import IntentTask


class AgentExecutionSnapshot(BaseModel):
    """Agent 执行快照，用于断点恢复"""
    
    # 会话标识
    conversation_id: str
    user_id: str
    
    # 原始请求
    original_query: str
    created_at: datetime
    
    # 任务计划
    task_plan: List[IntentTask]
    total_tasks: int
    
    # 执行状态
    completed_task_ids: List[str] = []
    current_task_id: Optional[str] = None
    failed_task_id: Optional[str] = None
    subtask_status: Dict[str, str] = {}  # pending | running | completed | failed
    
    # 已完成结果
    tool_results: Dict[str, Any] = {}  # task_id → result
    
    # 恢复控制
    resume_count: int = 0
    max_resume_count: int = 3
    
    # 时间戳
    last_checkpoint_at: datetime
    expires_at: datetime  # 默认 7 天
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 4.2.2 扩展 AsyncTask

python

```
# app/models/async_task.py 新增

class AsyncTask(Base):
    # ... 现有字段 ...
    
    # 新增字段
    conversation_id = Column(String(36), nullable=True, index=True)
    snapshot_json = Column(JSON, nullable=True)  # AgentExecutionSnapshot
    external_request_id = Column(String(64), nullable=True, index=True)
    
    # 新增状态
    # status: queued | running | waiting_external | success | failed | dead
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 4.3 核心实现

#### 4.3.1 快照存储

python

```
# app/repositories/agent_snapshot.py

import json
from datetime import datetime, timedelta
from redis.asyncio import Redis
from app.schemas.agent_snapshot import AgentExecutionSnapshot


class AgentSnapshotRepository:
    """Agent 快照读写（Redis + AsyncTask 兜底）"""
    
    def __init__(self, redis: Redis, async_task_repo):
        self.redis = redis
        self.async_task_repo = async_task_repo
    
    async def save(self, snapshot: AgentExecutionSnapshot) -> None:
        """保存快照到 Redis（快速读写）和 AsyncTask（持久化兜底）"""
        
        # 1. Redis 短期存储（TTL 7 天）
        key = f"agent:snapshot:{snapshot.conversation_id}"
        await self.redis.setex(
            key,
            7 * 24 * 3600,
            snapshot.model_dump_json()
        )
        
        # 2. AsyncTask 持久化兜底（用于 Worker 重启恢复）
        await self.async_task_repo.update_snapshot(
            conversation_id=snapshot.conversation_id,
            snapshot_json=snapshot.model_dump()
        )
    
    async def load(self, conversation_id: str) -> Optional[AgentExecutionSnapshot]:
        """加载快照，优先 Redis，失败时从 AsyncTask 恢复"""
        
        # 1. 尝试 Redis
        key = f"agent:snapshot:{conversation_id}"
        data = await self.redis.get(key)
        if data:
            return AgentExecutionSnapshot.model_validate_json(data)
        
        # 2. 兜底：从 AsyncTask 恢复
        task = await self.async_task_repo.get_by_conversation(conversation_id)
        if task and task.snapshot_json:
            return AgentExecutionSnapshot.model_validate(task.snapshot_json)
        
        return None
    
    async def delete(self, conversation_id: str) -> None:
        """快照完成或过期后清理"""
        key = f"agent:snapshot:{conversation_id}"
        await self.redis.delete(key)
        await self.async_task_repo.clear_snapshot(conversation_id)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 4.3.2 执行器改造

python

```
# app/services/customer_task_executor.py

class CustomerTaskExecutor:
    def __init__(self, snapshot_repo: AgentSnapshotRepository):
        self.snapshot_repo = snapshot_repo
    
    async def execute(self, conversation_id: str, query: str, user_id: str):
        """执行多意图任务，支持断点恢复"""
        
        # 1. 尝试恢复快照
        snapshot = await self.snapshot_repo.load(conversation_id)
        
        if snapshot and snapshot.resume_count < snapshot.max_resume_count:
            # 恢复执行
            return await self._resume(snapshot)
        
        # 2. 首次执行：生成任务计划
        task_plan = await self._plan_tasks(query, user_id)
        snapshot = AgentExecutionSnapshot(
            conversation_id=conversation_id,
            user_id=user_id,
            original_query=query,
            task_plan=task_plan,
            total_tasks=len(task_plan),
            subtask_status={t.task_id: "pending" for t in task_plan},
            last_checkpoint_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(days=7)
        )
        await self.snapshot_repo.save(snapshot)
        
        # 3. 执行任务循环
        return await self._execute_loop(snapshot)
    
    async def _execute_loop(self, snapshot: AgentExecutionSnapshot):
        """执行循环，每完成一个任务保存快照"""
        
        completed = set(snapshot.completed_task_ids)
        results = snapshot.tool_results.copy()
        
        for task in snapshot.task_plan:
            if task.task_id in completed:
                continue  # 已完成，跳过
            
            # 更新当前任务状态
            snapshot.current_task_id = task.task_id
            snapshot.subtask_status[task.task_id] = "running"
            await self.snapshot_repo.save(snapshot)
            
            try:
                result = await self._execute_task(task, snapshot)
                results[task.task_id] = result
                completed.add(task.task_id)
                
                # 每完成一个任务就保存快照
                snapshot.completed_task_ids = list(completed)
                snapshot.tool_results = results
                snapshot.subtask_status[task.task_id] = "completed"
                snapshot.last_checkpoint_at = datetime.utcnow()
                await self.snapshot_repo.save(snapshot)
                
            except Exception as e:
                # 记录失败，保存快照供人工介入
                snapshot.subtask_status[task.task_id] = "failed"
                snapshot.failed_task_id = task.task_id
                await self.snapshot_repo.save(snapshot)
                raise
        
        # 全部完成，清理快照
        await self.snapshot_repo.delete(snapshot.conversation_id)
        return self._aggregate_results(results)
    
    async def _resume(self, snapshot: AgentExecutionSnapshot):
        """从快照恢复执行"""
        snapshot.resume_count += 1
        snapshot.last_checkpoint_at = datetime.utcnow()
        await self.snapshot_repo.save(snapshot)
        return await self._execute_loop(snapshot)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 4.3.3 Worker 启动恢复

python

```
# workers/agent_recovery_worker.py

import asyncio
from app.services.customer_task_executor import CustomerTaskExecutor
from app.repositories.async_task import AsyncTaskRepository


class AgentRecoveryWorker:
    """启动时恢复中断的 Agent 任务"""
    
    async def recover_pending_tasks(self):
        """检查并恢复 pending/running/waiting_external 状态的 Agent 任务"""
        
        # 1. 查询未完成的 Agent 任务
        pending_tasks = await AsyncTaskRepository.list_by_condition(
            task_type="agent_execution",
            status=["queued", "running", "waiting_external"]
        )
        
        for task in pending_tasks:
            if not task.conversation_id:
                continue
            
            # 2. 检查是否有快照
            snapshot = await AgentSnapshotRepository.load(task.conversation_id)
            if not snapshot:
                # 无快照，重新入队
                await self._requeue_task(task)
                continue
            
            # 3. 根据状态决定恢复策略
            if snapshot.subtask_status.get(snapshot.current_task_id) == "running":
                # 超时保护：如果 running 超过 5 分钟，标记为 failed
                if (datetime.utcnow() - snapshot.last_checkpoint_at).seconds > 300:
                    snapshot.subtask_status[snapshot.current_task_id] = "failed"
                    await AgentSnapshotRepository.save(snapshot)
            
            # 4. 重新执行恢复
            executor = CustomerTaskExecutor(AgentSnapshotRepository(...))
            await executor._resume(snapshot)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 4.4 验收标准





| 场景                            | 预期行为                     |
| ------------------------------- | ---------------------------- |
| Worker 执行第 3 个任务时被 kill | 重启后从第 3 个任务继续      |
| 客户页面刷新                    | 展示已完成的中间结果，不丢失 |
| 同一会话重复提交                | 复用已有快照，不重复执行     |
| 快照过期（7 天）                | 自动清理，客户需重新提问     |
| 恢复超过 3 次                   | 标记失败，转人工处理         |

## 5. Phase 2：LangGraph Checkpointer 接入

> ✅ **2026-08-29 已接入**：`app/agents/checkpointer.py`（`memory_checkpointer` / `postgres_checkpointer` / `thread_config` / `with_interrupt`）；`SupervisorGraph.build(checkpointer=..., interrupt_before=..., interrupt_after=...)` 已支持。**MemorySaver 可用并测试通过**（`tests/test_agent_graph.py` 覆盖"summarize 前中断→恢复续跑→完成"）。**AsyncPostgresSaver 代码就绪，需装 `langgraph-checkpoint-postgres` 额外包 + DB**（本环境未装未跑）。下方方案为完整设计说明。

### 5.1 目标

客服 Agent 的多轮 LLM 推理（ReAct 循环）可以中断恢复，避免重复消耗 Token。

### 5.2 数据模型

LangGraph 原生使用 PostgreSQL 存储 Checkpoint：

sql

```
-- 由 AsyncPostgresSaver 自动创建
-- 表名：checkpoints, checkpoint_writes, checkpoint_blobs
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 5.3 核心实现

#### 5.3.1 启用 Checkpointer

python

```
# app/agents/graph.py

from langgraph.checkpoint.postgres import AsyncPostgresSaver
from app.db.session import async_engine

# 创建 Checkpointer
checkpointer = AsyncPostgresSaver(async_engine)

# 在编译 Graph 时传入
async def build_customer_agent_graph():
    workflow = StateGraph(AgentState)
    # ... 添加节点和边 ...
    return workflow.compile(checkpointer=checkpointer)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 5.3.2 执行时传入 thread_id

python

```
# app/services/customer_agent_execution.py

class CustomerAgentExecutionService:
    def __init__(self, graph):
        self.graph = graph
    
    async def execute(self, conversation_id: str, query: str):
        config = {
            "configurable": {
                "thread_id": conversation_id  # 会话 ID 作为恢复标识
            }
        }
        
        # 首次执行
        result = await self.graph.ainvoke(
            {"messages": [HumanMessage(content=query)]},
            config=config
        )
        
        # 断点恢复时，用同一个 thread_id 再次调用
        # LangGraph 自动从上次中断处继续，不重放已完成的步骤
        # result = await self.graph.ainvoke(None, config=config)
        
        return result
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 5.3.3 Checkpoint 清理

python

```
# workers/checkpoint_cleanup_worker.py

from datetime import datetime, timedelta


class CheckpointCleanupWorker:
    """定期清理过期的 Checkpoint"""
    
    async def run(self):
        cutoff = datetime.utcnow() - timedelta(days=7)
        
        # LangGraph 原生清理接口（需扩展）
        await checkpointer.delete_threads_before(cutoff)
        
        # 或直接 SQL 清理
        async with async_engine.connect() as conn:
            await conn.execute(
                "DELETE FROM checkpoints WHERE checkpoint_at < $1",
                cutoff
            )
            await conn.commit()
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 5.4 与 Phase 1 的关系





| 层级                 | 使用场景               | 存储              |
| -------------------- | ---------------------- | ----------------- |
| Phase 1 快照         | 多意图任务拆分（高级） | Redis + AsyncTask |
| Phase 2 Checkpointer | LLM ReAct 循环（低级） | PostgreSQL        |

**两者互补**：

- Phase 1 负责“哪些子任务已完成”
- Phase 2 负责“当前 LLM 推理到哪一步”

### 5.5 验收标准





| 场景                                  | 预期行为            |
| ------------------------------------- | ------------------- |
| ReAct 循环执行到第 2 轮时 Worker 崩溃 | 重启后从第 2 轮继续 |
| 恢复时不重复调用前几轮的 LLM          | Token 消耗不增加    |
| PostgreSQL `checkpoints` 表有对应记录 | 可审计              |
| 7 天后自动清理                        | 表不膨胀            |

## 6. Phase 3：AML 批次完整恢复

### 6.1 目标

`DailyAmlScanService` 扫描全客户时，单个客户失败不影响其他客户，且支持定向补跑。

### 6.2 现有基础

- `AmlBatchRun` 已记录批次状态和 `error_summary`
- `scripts/test_aml_batch_retry_http.py` 已验证补跑入口
- `POST /risk-alerts/batches/{batch_id}/retry` 已实现

### 6.3 核心实现（补齐缺口）

#### 6.3.1 批次内客户级断点

python

```
# app/services/daily_aml_scan_service.py

class DailyAmlScanService:
    async def run(self, batch_id: str, customers: List[Customer]):
        batch = await self._get_batch(batch_id)
        
        # 恢复：从元数据读取已成功客户
        metadata = batch.metadata or {}
        succeeded_ids = set(metadata.get("succeeded_customer_ids", []))
        
        for customer in customers:
            if customer.id in succeeded_ids:
                continue  # 已成功，跳过
            
            try:
                await self._scan_customer(customer, batch)
                succeeded_ids.add(customer.id)
                
                # 每成功一个客户就更新元数据
                metadata["succeeded_customer_ids"] = list(succeeded_ids)
                await self._update_batch_metadata(batch, metadata)
                
            except Exception as e:
                # 记录失败，继续下一个客户
                await self._record_failure(batch, customer.id, str(e))
                # 不中断批次
        
        # 更新最终状态
        await self._finalize_batch(batch, succeeded_ids)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 6.3.2 迟到流水补扫幂等

python

```
# app/services/daily_aml_scan_service.py

async def _get_late_rescan_batch_key(
    source_batch_id: str,
    window_start: datetime,
    window_end: datetime,
    days: int
) -> str:
    """迟到流水补扫使用独立的 batch_key，不覆盖原批次"""
    return f"daily_aml:late_rescan:{source_batch_id}:{days}:{window_start.isoformat()}:{window_end.isoformat()}"
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 6.4 验收标准





| 场景                         | 预期行为                               |
| ---------------------------- | -------------------------------------- |
| 5000 客户扫描，第 300 个失败 | 批次状态为 `completed_with_errors`     |
| 调用 `retry`                 | 从第 300 个客户继续，不重扫前 299 个   |
| 迟到流水补扫                 | 使用独立 `batch_key`，不覆盖原批次预警 |
| 补扫重复调用                 | 幂等复用已有补扫批次                   |

## 7. Phase 4：Webhook 唤醒长时等待

### 7.1 目标

Agent 调用外部 API（如银行接口、人工审核）需要等待数分钟甚至数小时，进程不阻塞。

### 7.2 数据模型

python

```
# app/schemas/external_wait.py

class ExternalWaitState(BaseModel):
    """外部等待状态"""
    conversation_id: str
    task_id: str
    external_request_id: str
    callback_url: str
    status: str  # waiting | completed | failed | timeout
    created_at: datetime
    expires_at: datetime  # 默认 24 小时
    result: Optional[Any] = None
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 7.3 核心实现

#### 7.3.1 发起外部调用

python

```
# app/services/customer_task_executor.py

async def execute_external_call(task: IntentTask, snapshot: AgentExecutionSnapshot):
    """执行需要外部等待的任务"""
    
    # 1. 生成外部请求 ID
    external_request_id = f"ext_{uuid4().hex[:16]}"
    
    # 2. 保存等待状态
    wait_state = ExternalWaitState(
        conversation_id=snapshot.conversation_id,
        task_id=task.task_id,
        external_request_id=external_request_id,
        callback_url=f"/api/v1/webhooks/external/{external_request_id}",
        status="waiting",
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=24)
    )
    await self._save_wait_state(wait_state)
    
    # 3. 更新快照状态为 waiting_external
    snapshot.subtask_status[task.task_id] = "waiting_external"
    snapshot.external_request_id = external_request_id
    await self.snapshot_repo.save(snapshot)
    
    # 4. 发起外部调用（异步，不等待）
    await external_service.submit(
        request_id=external_request_id,
        payload=task.payload,
        callback_url=wait_state.callback_url
    )
    
    # 5. 返回等待状态，释放 Worker
    return AgentResult(
        status="waiting_external",
        message="已提交外部请求，等待响应",
        data={"external_request_id": external_request_id}
    )
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

#### 7.3.2 Webhook 回调唤醒

python

```
# app/api/webhooks.py

from fastapi import APIRouter, BackgroundTasks

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.post("/external/{request_id}")
async def external_callback(
    request_id: str,
    payload: Any,
    background_tasks: BackgroundTasks
):
    """外部系统回调，唤醒等待的 Agent"""
    
    # 1. 查找等待状态
    wait_state = await ExternalWaitRepository.get(request_id)
    if not wait_state:
        raise HTTPException(404, "EXTERNAL_REQUEST_NOT_FOUND")
    
    # 2. 更新状态
    wait_state.status = "completed"
    wait_state.result = payload
    await ExternalWaitRepository.save(wait_state)
    
    # 3. 后台恢复执行（不阻塞回调响应）
    background_tasks.add_task(
        resume_agent_execution,
        conversation_id=wait_state.conversation_id,
        external_result=payload
    )
    
    return {"status": "accepted"}


async def resume_agent_execution(conversation_id: str, external_result: Any):
    """恢复 Agent 执行"""
    
    # 1. 加载快照
    snapshot = await AgentSnapshotRepository.load(conversation_id)
    if not snapshot:
        return
    
    # 2. 恢复执行
    executor = CustomerTaskExecutor(...)
    result = await executor._resume_with_external_result(snapshot, external_result)
    
    # 3. 通知客户端（通过 WebSocket）
    await notify_client(conversation_id, result)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 7.4 验收标准





| 场景               | 预期行为                            |
| ------------------ | ----------------------------------- |
| Agent 调用外部 API | 立即返回 `waiting_external`，不阻塞 |
| 外部 Webhook 回调  | 从断点继续执行后续任务              |
| 24 小时无回调      | 自动超时，标记失败，转人工          |
| 客户 WebSocket     | 收到最终结果通知                    |

## 8. 实施优先级与工期估算

### 8.1 优先级排序





| 阶段        | 内容                   | 工期   | 紧急度 | 依赖                          |
| ----------- | ---------------------- | ------ | ------ | ----------------------------- |
| **Phase 1** | Agent 执行快照         | 2-3 天 | 🔴 高   | 已有 `AsyncTask` + Redis      |
| **Phase 3** | AML 批次完整恢复       | 1-2 天 | 🔴 高   | 已有 `AmlBatchRun` + 补跑接口 |
| **Phase 2** | LangGraph Checkpointer | 3-5 天 | 🟡 中   | 已安装 `langgraph`            |
| **Phase 4** | Webhook 唤醒           | 3-5 天 | 🟢 低   | 已有 Outbox + WebSocket       |

### 8.2 建议实施顺序

text

```
Week 1:
  ├── Phase 1: Agent 执行快照（2-3 天）
  └── Phase 3: AML 批次完整恢复（1-2 天）

Week 2:
  ├── Phase 2: LangGraph Checkpointer（3-5 天）
  └── 联调 + 回归测试

Week 3:
  └── Phase 4: Webhook 唤醒（3-5 天，按业务需求决定是否实施）
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 8.3 风险评估





| 风险              | 影响                | 缓解措施             |
| ----------------- | ------------------- | -------------------- |
| Redis 快照丢失    | Agent 无法恢复      | AsyncTask 兜底持久化 |
| Checkpoint 表膨胀 | PostgreSQL 性能下降 | 定期清理 Worker      |
| Webhook 回调丢失  | 外部等待永久挂起    | 超时机制 + 人工兜底  |
| 快照版本不兼容    | 旧快照无法恢复      | 版本字段 + 迁移脚本  |

## 9. 代码改动清单

### 9.1 Phase 1 改动

text

```
新增文件：
  app/schemas/agent_snapshot.py          # AgentExecutionSnapshot Schema
  app/repositories/agent_snapshot.py     # 快照读写（Redis + AsyncTask）
  workers/agent_recovery_worker.py       # 启动时恢复 pending 任务
  tests/test_agent_snapshot.py           # 快照单元测试
  scripts/test_agent_recovery_http.py    # 模拟 Worker 崩溃后恢复

修改文件：
  app/models/async_task.py               # 增加 conversation_id, snapshot_json
  app/schemas/async_task.py              # 增加 snapshot 字段
  app/services/customer_task_executor.py # 增加快照保存/恢复逻辑
  app/api/customer_service.py            # 增加恢复状态接口

迁移文件：
  migrations/versions/0022_async_task_snapshot.py
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 9.2 Phase 2 改动

text

```
新增文件：
  app/agents/checkpointer.py             # LangGraph Checkpointer 配置
  workers/checkpoint_cleanup_worker.py   # Checkpoint 清理 Worker

修改文件：
  app/agents/graph.py                    # 传入 checkpointer
  app/services/customer_agent_execution.py # 使用 thread_id
  docker-compose.p0.yml                  # 增加 checkpoint-cleanup-worker
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 9.3 Phase 3 改动

text

```
修改文件：
  app/services/daily_aml_scan_service.py # 增加客户级断点
  app/models/aml.py                      # 增加 metadata.succeeded_customer_ids
  app/api/risk.py                        # 增加补跑状态查询

新增文件：
  scripts/test_aml_batch_recovery.py     # 批次恢复 HTTP 测试
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 9.4 Phase 4 改动

text

```
新增文件：
  app/schemas/external_wait.py           # ExternalWaitState Schema
  app/repositories/external_wait.py      # 等待状态存储
  app/api/webhooks.py                    # Webhook 回调接口

修改文件：
  app/services/customer_task_executor.py # 增加外部调用和恢复逻辑
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 10. 验收标准

### 10.1 Phase 1 验收





| 测试场景           | 验收命令                              | 预期结果                     |
| ------------------ | ------------------------------------- | ---------------------------- |
| 多意图任务正常执行 | `scripts/test_agent_snapshot.py`      | 快照保存，最终结果正确       |
| Worker 崩溃恢复    | `scripts/test_agent_recovery_http.py` | 从断点继续，不重跑已完成任务 |
| 快照过期清理       | 检查 Redis TTL                        | 7 天后自动清理               |
| 恢复超过 3 次      | `scripts/test_agent_recovery_http.py` | 标记失败，转人工             |

### 10.2 Phase 2 验收





| 测试场景                   | 验收命令                               | 预期结果           |
| -------------------------- | -------------------------------------- | ------------------ |
| ReAct 循环中断恢复         | `scripts/test_langgraph_checkpoint.py` | 从 Checkpoint 继续 |
| PostgreSQL Checkpoint 写入 | 查询 `checkpoints` 表                  | 有对应记录         |
| Checkpoint 清理            | 检查 `checkpoints` 表                  | 7 天后自动删除     |

### 10.3 Phase 3 验收





| 测试场景     | 验收命令                                     | 预期结果                |
| ------------ | -------------------------------------------- | ----------------------- |
| 批次部分失败 | `scripts/test_aml_batch_recovery.py`         | `completed_with_errors` |
| 补跑失败客户 | `POST /risk-alerts/batches/{id}/retry`       | 从失败客户继续          |
| 迟到流水补扫 | `POST /risk-alerts/batches/{id}/late-rescan` | 独立批次，不覆盖原证据  |

### 10.4 Phase 4 验收





| 测试场景       | 验收命令                              | 预期结果         |
| -------------- | ------------------------------------- | ---------------- |
| 外部调用不阻塞 | Agent 请求立即返回 `waiting_external` | Worker 释放      |
| Webhook 唤醒   | `POST /webhooks/external/{id}`        | Agent 从断点继续 |
| 超时处理       | 24 小时无回调                         | 自动超时，转人工 |

## 11. 与现有文档的关联





| 文档                                          | 关联内容                            |
| --------------------------------------------- | ----------------------------------- |
| `docs/DEFENSE_SYSTEM_DESIGN_AND_EVOLUTION.md` | 补充断点续跑章节（7.9.11）          |
| `docs/PLAN.md`                                | 新增 Phase 5：断点续跑实施计划      |
| `docs/ISSUES.md`                              | 新增 ISSUE-0402：Agent 断点续跑实施 |
| `docs/TESTS.md`                               | 新增 TEST-0400 ~ TEST-0405 验收记录 |
| `docs/AGENT_FUNCTIONS_AND_BUSINESS_DESIGN.md` | 补充恢复机制说明                    |

## 12. 总结

### 12.1 核心设计原则

1. **渐进式增强**：不推翻现有架构，在已有组件上叠加恢复能力
2. **分层恢复**：会话级、任务级、批次级、外部等待级各司其职
3. **至少一次投递**：Outbox + Redis Streams 保证事件不丢失
4. **幂等是关键**：所有恢复操作必须幂等，防止重复执行
5. **审计可追溯**：所有恢复操作写入审计日志

### 12.2 一句话概括

> **断点续跑不是从零建设，而是把 AmlBatchRun 的批次恢复、WorkOrder 的状态暂停、AsyncTask 的任务重试、LangGraph 的 Checkpointer 这四件事，统一到 Agent 执行层。**