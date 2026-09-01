# 多 Agent 共享记忆设计方案

> **文档用途**：技术方案设计、架构评审、代码改造指南
> **适用项目**：智能财富管理平台（Wealth Manager）
> **文档日期**：2026-08-13
> **状态**：设计完成，待实施

## 目录

1. 当前状态评估
2. 设计目标
3. 总体架构设计
4. 数据模型扩展
5. 核心实现
6. 聚合机制
7. 权限与安全
8. 实施路线图
9. 代码改动清单
10. 验收标准

## 1. 当前状态评估

### 1.1 现有 Agent 记忆孤岛





| Agent                  | 记忆存储                     | 作用域标签                   | 跨 Agent 共享 |
| ---------------------- | ---------------------------- | ---------------------------- | ------------- |
| `CustomerServiceAgent` | Redis 短期 + PostgreSQL 长期 | `conversation_id`, `user_id` | ❌ 不共享      |
| `AdvisorAgent`         | 无持久化记忆                 | `customer_id`                | ❌ 不共享      |
| `RiskAgent`            | 无持久化记忆                 | `customer_id`                | ❌ 不共享      |
| `DataAnalysisAgent`    | 无持久化记忆                 | 权限上下文                   | ❌ 不共享      |
| `BusinessAgent`        | 无持久化记忆                 | `customer_id`                | ❌ 不共享      |

### 1.2 已有可复用基础设施





| 组件                        | 文件位置                                  | 可复用能力         |
| --------------------------- | ----------------------------------------- | ------------------ |
| `CustomerMemory`            | `app/models/customer_memory.py`           | 客户级记忆存储基础 |
| `AgentCapabilityRegistry`   | `app/services/agent_capability.py`        | Agent 权限前置过滤 |
| `PermissionScopedCache`     | `app/services/permission_scoped_cache.py` | 权限感知缓存隔离   |
| `SubjectScopeService`       | `app/services/subject_scope.py`           | 授权 subject 范围  |
| `KnowledgeScope`            | `app/common/knowledge_scope.py`           | 知识库作用域过滤   |
| `AuditLog`                  | `app/models/audit_log.py`                 | 审计追溯           |
| `AsyncTask` + `OutboxEvent` | `app/models/async_task.py`                | 异步记忆提取       |

### 1.3 已踩的坑（与共享记忆相关）





| 问题                                       | 根因                | 启示                     |
| ------------------------------------------ | ------------------- | ------------------------ |
| `ISSUE-0095`：日度估值快照被旧派生覆盖     | 记忆过期机制缺失    | 共享记忆需要显式过期策略 |
| `ISSUE-0396`：bounded ReAct 审计不记录推理 | 来源追溯缺失        | 共享记忆必须记录来源     |
| `ISSUE-0316`：预警去重键不一致             | 多 Agent 写入无协调 | 共享记忆需要统一写入 API |

## 2. 设计目标

### 2.1 核心目标

1. **共享**：一个 Agent 的观察可被其他 Agent 检索复用
2. **隔离**：Agent 不能越权读写其他 Agent 的私有记忆
3. **可追溯**：每条记忆有来源、有时间、有作用域
4. **可演进**：从私有 → 共享 → 组织级认知，分阶段落地
5. **可审计**：所有读写操作记录审计日志

### 2.2 参考架构演进路线

文章总结的四阶段演进：

text

```
阶段一：单 Agent 私有记忆（你已超越）
阶段二：消息传递式协作（你正在做）
阶段三：共享池 + 作用域标签（你应该做）
阶段四：治理型共享记忆（未来方向）
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 3. 总体架构设计

### 3.1 分层模型

借鉴文章提出的 I/O → 缓存 → 内存 三层模型：

text

```
┌─────────────────────────────────────────────────────────────────┐
│                      Agent 执行层                               │
│         CustomerService / Advisor / Risk / Analysis / Business │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      内存层（会话级工作记忆）                    │
│              Redis：当前会话上下文、TaskPlan、临时状态           │
│              生命周期：会话结束即清除                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      缓存层（Agent 级短期记忆）                  │
│              Redis：高频访问记忆、近期检索结果                   │
│              生命周期：TTL 7 天                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      I/O 层（持久化存储）                        │
│              PostgreSQL：所有记忆的持久化存储                    │
│              生命周期：按过期策略清理                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    聚合层（组织级认知）                          │
│              离线 Worker：将个体观察聚合为组织级洞察             │
│              输出：组织级记忆（scope=organization）              │
└─────────────────────────────────────────────────────────────────┘
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 3.2 各层职责





| 层级   | 存储       | 生命周期     | 访问模式         |
| ------ | ---------- | ------------ | ---------------- |
| 内存层 | Redis      | 会话结束清除 | 读写，亚毫秒级   |
| 缓存层 | Redis      | 7 天 TTL     | 读为主，毫秒级   |
| I/O 层 | PostgreSQL | 按过期策略   | 读写，数十毫秒级 |
| 聚合层 | PostgreSQL | 永久         | 离线批量写入     |

## 4. 数据模型扩展

### 4.1 扩展 `CustomerMemory` 模型

python

```
# app/models/customer_memory.py

from sqlalchemy import Column, String, DateTime, Float, JSON, Enum
from datetime import datetime
import enum


class MemoryScope(str, enum.Enum):
    """记忆作用域"""
    PRIVATE = "private"          # 仅写入 Agent 可见
    SHARED = "shared"            # 同客户的所有 Agent 可见
    ORGANIZATION = "organization" # 所有 Agent 可见（组织级洞察）


class MemoryType(str, enum.Enum):
    """记忆类型"""
    OBSERVATION = "observation"      # Agent 直接观察
    INFERENCE = "inference"          # Agent 推理得出
    EXTERNAL_INPUT = "external_input" # 外部输入
    AGGREGATED_INSIGHT = "aggregated_insight"  # 聚合洞察


class CustomerMemory(Base):
    """客户记忆（扩展版）"""
    __tablename__ = "customer_memory"
    
    # === 现有字段（保留） ===
    id = Column(String(36), primary_key=True)
    user_id = Column(String(36), nullable=False, index=True)
    conversation_id = Column(String(36), index=True)
    content = Column(String(4096), nullable=False)
    embedding = Column(Vector(1024), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # === 新增字段 ===
    agent_type = Column(String(32), nullable=False)  # customer_service | advisor | risk | analysis | business
    memory_type = Column(Enum(MemoryType), nullable=False)
    scope = Column(Enum(MemoryScope), nullable=False, default=MemoryScope.PRIVATE)
    
    # 来源追溯
    source_id = Column(String(36), nullable=True)  # conversation_id / task_id / work_order_id
    source_type = Column(String(32), nullable=True)  # conversation | task | workorder | aml_batch
    
    # 时间语义
    valid_from = Column(DateTime, nullable=False, default=datetime.utcnow)
    valid_to = Column(DateTime, nullable=True)  # None 表示当前有效
    expires_at = Column(DateTime, nullable=True)  # 硬过期时间
    
    # 置信度
    confidence = Column(Float, nullable=False, default=0.5)
    
    # 消费追踪
    consumed_by = Column(JSON, nullable=True, default=list)  # 已消费此记忆的 Agent 列表
    
    # 关联
    related_memory_ids = Column(JSON, nullable=True, default=list)
    superseded_by = Column(String(36), nullable=True)  # 被哪条新记忆替代
    conflict_with = Column(JSON, nullable=True, default=list)  # 冲突的记忆 ID 列表
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 4.2 迁移脚本

python

```
# migrations/versions/0023_shared_memory_extensions.py

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON


def upgrade():
    op.add_column('customer_memory', sa.Column('agent_type', sa.String(32), nullable=True))
    op.add_column('customer_memory', sa.Column('memory_type', sa.String(32), nullable=True))
    op.add_column('customer_memory', sa.Column('scope', sa.String(32), nullable=True))
    op.add_column('customer_memory', sa.Column('source_id', sa.String(36), nullable=True))
    op.add_column('customer_memory', sa.Column('source_type', sa.String(32), nullable=True))
    op.add_column('customer_memory', sa.Column('valid_from', sa.DateTime, nullable=True))
    op.add_column('customer_memory', sa.Column('valid_to', sa.DateTime, nullable=True))
    op.add_column('customer_memory', sa.Column('expires_at', sa.DateTime, nullable=True))
    op.add_column('customer_memory', sa.Column('confidence', sa.Float, nullable=True))
    op.add_column('customer_memory', sa.Column('consumed_by', JSON, nullable=True))
    op.add_column('customer_memory', sa.Column('related_memory_ids', JSON, nullable=True))
    op.add_column('customer_memory', sa.Column('superseded_by', sa.String(36), nullable=True))
    op.add_column('customer_memory', sa.Column('conflict_with', JSON, nullable=True))
    
    # 回填默认值
    op.execute("UPDATE customer_memory SET agent_type = 'customer_service', memory_type = 'observation', scope = 'private', confidence = 0.5 WHERE agent_type IS NULL")
    
    # 设置非空约束
    op.alter_column('customer_memory', 'agent_type', nullable=False)
    op.alter_column('customer_memory', 'memory_type', nullable=False)
    op.alter_column('customer_memory', 'scope', nullable=False)
    op.alter_column('customer_memory', 'confidence', nullable=False)
    
    # 创建索引
    op.create_index('ix_customer_memory_agent_type', 'customer_memory', ['agent_type'])
    op.create_index('ix_customer_memory_scope', 'customer_memory', ['scope'])
    op.create_index('ix_customer_memory_valid_to', 'customer_memory', ['valid_to'])
    op.create_index('ix_customer_memory_expires_at', 'customer_memory', ['expires_at'])


def downgrade():
    op.drop_index('ix_customer_memory_expires_at')
    op.drop_index('ix_customer_memory_valid_to')
    op.drop_index('ix_customer_memory_scope')
    op.drop_index('ix_customer_memory_agent_type')
    op.drop_column('customer_memory', 'conflict_with')
    op.drop_column('customer_memory', 'superseded_by')
    op.drop_column('customer_memory', 'related_memory_ids')
    op.drop_column('customer_memory', 'consumed_by')
    op.drop_column('customer_memory', 'confidence')
    op.drop_column('customer_memory', 'expires_at')
    op.drop_column('customer_memory', 'valid_to')
    op.drop_column('customer_memory', 'valid_from')
    op.drop_column('customer_memory', 'source_type')
    op.drop_column('customer_memory', 'source_id')
    op.drop_column('customer_memory', 'scope')
    op.drop_column('customer_memory', 'memory_type')
    op.drop_column('customer_memory', 'agent_type')
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 5. 核心实现

### 5.1 统一写入 API

所有 Agent 通过统一 API 写入记忆，确保权限校验和审计。

python

```
# app/services/shared_memory_service.py

from typing import Optional, List
from datetime import datetime, timedelta
from uuid import uuid4
from app.models.customer_memory import CustomerMemory, MemoryScope, MemoryType
from app.schemas.memory import MemoryWriteRequest, MemoryWriteResponse


class SharedMemoryService:
    """共享记忆服务 - 所有 Agent 的统一写入入口"""
    
    def __init__(self, memory_repo, audit_repo, permission_checker):
        self.memory_repo = memory_repo
        self.audit_repo = audit_repo
        self.permission_checker = permission_checker
    
    async def write(
        self,
        request: MemoryWriteRequest,
        current_agent: str,
        current_user_id: str
    ) -> MemoryWriteResponse:
        """写入记忆，统一校验"""
        
        # 1. 权限校验：该 Agent 是否有权写入该作用域
        if not await self.permission_checker.can_write(
            agent=current_agent,
            scope=request.scope,
            customer_id=request.customer_id
        ):
            raise PermissionError(f"Agent {current_agent} cannot write to scope {request.scope}")
        
        # 2. 如果 scope=shared，检查是否已有同主题的活跃记忆
        if request.scope == MemoryScope.SHARED:
            existing = await self._find_active_memory(
                customer_id=request.customer_id,
                topic=request.topic,
                agent_type=current_agent
            )
            if existing:
                # 置信度更高的新记忆替代旧记忆
                if request.confidence > existing.confidence:
                    existing.valid_to = datetime.utcnow()
                    existing.superseded_by = new_memory_id
                    await self.memory_repo.save(existing)
        
        # 3. 创建新记忆
        memory = CustomerMemory(
            id=str(uuid4()),
            user_id=request.customer_id,
            content=request.content,
            agent_type=current_agent,
            memory_type=request.memory_type,
            scope=request.scope,
            source_id=request.source_id,
            source_type=request.source_type,
            valid_from=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(days=request.ttl_days or 30),
            confidence=request.confidence or 0.5,
            consumed_by=[]
        )
        
        # 4. 持久化
        await self.memory_repo.save(memory)
        
        # 5. 审计
        await self.audit_repo.save(
            action="memory.write",
            resource_type="customer_memory",
            resource_id=memory.id,
            detail={
                "agent": current_agent,
                "scope": request.scope,
                "memory_type": request.memory_type,
                "customer_id": request.customer_id
            }
        )
        
        # 6. 异步 Embedding（复用现有知识库 Worker）
        await self._queue_embedding(memory.id, memory.content)
        
        return MemoryWriteResponse(
            memory_id=memory.id,
            scope=memory.scope,
            created_at=memory.created_at
        )
    
    async def _find_active_memory(self, customer_id: str, topic: str, agent_type: str):
        """查找同一主题的活跃记忆（用于冲突检测）"""
        # 使用 pgvector 语义相似度或关键词匹配
        # 返回置信度最高的匹配
        pass
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 5.2 统一检索 API

python

```
# app/services/shared_memory_service.py（续）

class SharedMemoryService:
    # ... 前面代码 ...
    
    async def search(
        self,
        query: str,
        scope: Optional[MemoryScope] = None,
        agent_type: Optional[str] = None,
        customer_id: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        current_agent: str = None,
        current_user_id: str = None,
        limit: int = 10
    ) -> List[CustomerMemory]:
        """检索共享记忆，统一权限过滤"""
        
        # 1. 构建检索条件
        filters = {}
        
        # 作用域过滤：Agent 只能读自己作用域内的记忆
        allowed_scopes = await self.permission_checker.get_readable_scopes(current_agent)
        if scope:
            if scope not in allowed_scopes:
                raise PermissionError(f"Agent {current_agent} cannot read scope {scope}")
            filters["scope"] = scope
        else:
            filters["scope__in"] = allowed_scopes
        
        if agent_type:
            filters["agent_type"] = agent_type
        if customer_id:
            # 客户级数据隔离
            if not await self.permission_checker.can_read_customer(current_agent, customer_id):
                raise PermissionError(f"Agent {current_agent} cannot access customer {customer_id}")
            filters["user_id"] = customer_id
        if memory_type:
            filters["memory_type"] = memory_type
        
        # 2. 过期过滤
        filters["valid_to__isnull"] = True
        filters["expires_at__gt"] = datetime.utcnow()
        
        # 3. 检索（优先向量检索，降级关键词）
        if query:
            results = await self._vector_search(query, filters, limit)
        else:
            results = await self._keyword_search(filters, limit)
        
        # 4. 记录消费
        for memory in results:
            if current_agent not in (memory.consumed_by or []):
                memory.consumed_by = (memory.consumed_by or []) + [current_agent]
                await self.memory_repo.save(memory)
        
        return results
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 5.3 记忆检索 API 路由

python

```
# app/api/shared_memory.py

from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from app.schemas.memory import MemorySearchRequest, MemorySearchResponse
from app.services.shared_memory_service import SharedMemoryService
from app.common.security.auth import get_current_user, User
from app.services.agent_capability import AgentCapabilityRegistry

router = APIRouter(prefix="/api/v1/shared-memory", tags=["shared_memory"])


@router.post("/search")
async def search_shared_memory(
    request: MemorySearchRequest,
    current_user: User = Depends(get_current_user)
):
    """检索共享记忆"""
    
    # 1. 确定当前 Agent 类型
    agent_type = request.agent_type or _infer_agent_from_user(current_user)
    
    # 2. 权限前置校验（复用 AgentCapabilityRegistry）
    if not AgentCapabilityRegistry.can_access_memory(agent_type, request.scope):
        raise HTTPException(403, "MEMORY_ACCESS_DENIED")
    
    # 3. 检索
    service = SharedMemoryService(...)
    results = await service.search(
        query=request.query,
        scope=request.scope,
        agent_type=request.agent_type,
        customer_id=request.customer_id,
        memory_type=request.memory_type,
        current_agent=agent_type,
        current_user_id=current_user.id,
        limit=request.limit or 10
    )
    
    return MemorySearchResponse(
        items=results,
        total=len(results)
    )


@router.post("/write")
async def write_shared_memory(
    request: MemoryWriteRequest,
    current_user: User = Depends(get_current_user)
):
    """写入共享记忆"""
    
    agent_type = request.agent_type or _infer_agent_from_user(current_user)
    
    service = SharedMemoryService(...)
    result = await service.write(
        request=request,
        current_agent=agent_type,
        current_user_id=current_user.id
    )
    
    return result
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 5.4 Schema 定义

python

```
# app/schemas/memory.py

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from app.models.customer_memory import MemoryScope, MemoryType


class MemoryWriteRequest(BaseModel):
    """写入记忆请求"""
    customer_id: str
    content: str
    agent_type: Optional[str] = None
    memory_type: MemoryType
    scope: MemoryScope = MemoryScope.PRIVATE
    source_id: Optional[str] = None
    source_type: Optional[str] = None
    ttl_days: Optional[int] = 30
    confidence: Optional[float] = 0.5
    topic: Optional[str] = None  # 用于冲突检测


class MemoryWriteResponse(BaseModel):
    """写入记忆响应"""
    memory_id: str
    scope: MemoryScope
    created_at: datetime


class MemorySearchRequest(BaseModel):
    """检索记忆请求"""
    query: str
    scope: Optional[MemoryScope] = None
    agent_type: Optional[str] = None
    customer_id: Optional[str] = None
    memory_type: Optional[MemoryType] = None
    limit: int = 10


class MemorySearchResponse(BaseModel):
    """检索记忆响应"""
    items: List[dict]
    total: int
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 6. 聚合机制

### 6.1 设计目标

多个 Agent 的零散观察 → 组织级认知

text

```
客服 Agent：这类客户总问同一个问题
技术 Agent：同一类 bug 反复出现
风险 Agent：同一类资金模式频繁触发预警

        ↓ 聚合层

组织级洞察：这类用户的使用场景存在系统性缺陷，建议产品改进
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 6.2 聚合 Worker

python

```
# workers/memory_aggregation_worker.py

import asyncio
from datetime import datetime, timedelta
from typing import List, Dict
from collections import defaultdict
from app.services.shared_memory_service import SharedMemoryService
from app.repositories.memory import MemoryRepository


class MemoryAggregationWorker:
    """记忆聚合 Worker - 将个体观察聚合为组织级认知"""
    
    def __init__(self, memory_repo: MemoryRepository, llm_client):
        self.memory_repo = memory_repo
        self.llm_client = llm_client
    
    async def run(self):
        """运行聚合任务"""
        
        # 1. 读取最近 7 天的共享记忆
        observations = await self.memory_repo.list(
            scope="shared",
            memory_type="observation",
            created_after=datetime.utcnow() - timedelta(days=7)
        )
        
        if len(observations) < 5:
            return  # 样本不足
        
        # 2. 按客户类型聚类
        clusters = self._cluster_observations(observations)
        
        # 3. 对每个聚类生成洞察
        for cluster in clusters:
            if len(cluster) < 3:
                continue  # 样本不足
            
            insight = await self._generate_insight(cluster)
            
            # 4. 写入组织级记忆
            await self._write_organization_memory(insight, cluster)
    
    def _cluster_observations(self, observations: List) -> List[List]:
        """聚类观察（按主题 + 客户类型）"""
        clusters = defaultdict(list)
        
        for obs in observations:
            # 从 content 提取主题关键词
            topic = self._extract_topic(obs.content)
            customer_type = obs.metadata.get("customer_type", "unknown")
            key = f"{topic}:{customer_type}"
            clusters[key].append(obs)
        
        return list(clusters.values())
    
    async def _generate_insight(self, cluster: List) -> str:
        """使用 LLM 生成洞察摘要"""
        
        observations_text = "\n".join([
            f"- {obs.content} (from {obs.agent_type})"
            for obs in cluster[:10]
        ])
        
        prompt = f"""
        以下是一组多个 Agent 对同一类客户/问题的观察：
        
        {observations_text}
        
        请总结：
        1. 这些观察的共同主题是什么？
        2. 这个模式可能的原因是什么？
        3. 建议的组织级行动是什么？
        
        输出简洁的洞察摘要（不超过 200 字）：
        """
        
        response = await self.llm_client.complete(prompt)
        return response.content
    
    async def _write_organization_memory(self, insight: str, source_observations: List):
        """写入组织级记忆"""
        
        memory = CustomerMemory(
            id=str(uuid4()),
            user_id=None,  # 组织级记忆不绑定具体客户
            content=insight,
            agent_type="aggregation",
            memory_type="aggregated_insight",
            scope="organization",
            source_id=",".join([obs.id for obs in source_observations]),
            source_type="aggregation",
            valid_from=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(days=90),
            confidence=0.7,
            consumed_by=[]
        )
        
        await self.memory_repo.save(memory)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 6.3 定时调度

python

```
# workers/aggregation_scheduler.py

from apscheduler.schedulers.asyncio import AsyncIOScheduler


async def start_aggregation_scheduler():
    """启动聚合调度器"""
    
    scheduler = AsyncIOScheduler()
    worker = MemoryAggregationWorker(...)
    
    # 每天凌晨 2 点运行
    scheduler.add_job(
        worker.run,
        "cron",
        hour=2,
        minute=0
    )
    scheduler.start()
    
    print("Memory aggregation scheduler started")
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 6.4 Compose 配置

yaml

```
# docker-compose.p0.yml 新增

services:
  aggregation-worker:
    build:
      context: .
      dockerfile: Dockerfile.api
    command: python -m workers.memory_aggregation_worker
    environment:
      - DATABASE_URL=${DATABASE_URL}
      - REDIS_URL=${REDIS_URL}
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    profiles:
      - aggregation
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 7. 权限与安全

### 7.1 作用域访问矩阵





| Agent                  | 可写作用域          | 可读作用域                          |
| ---------------------- | ------------------- | ----------------------------------- |
| `CustomerServiceAgent` | `private`, `shared` | `private`, `shared`, `organization` |
| `AdvisorAgent`         | `private`, `shared` | `private`, `shared`, `organization` |
| `RiskAgent`            | `private`, `shared` | `private`, `shared`, `organization` |
| `DataAnalysisAgent`    | `shared`            | `shared`, `organization`            |
| `BusinessAgent`        | `private`           | `private`, `organization`           |

### 7.2 权限校验实现

python

```
# app/services/memory_permission.py

from typing import List, Optional
from app.models.customer_memory import MemoryScope


class MemoryPermissionChecker:
    """共享记忆权限校验"""
    
    # Agent 可写作用域
    WRITE_PERMISSIONS = {
        "customer_service": [MemoryScope.PRIVATE, MemoryScope.SHARED],
        "advisor": [MemoryScope.PRIVATE, MemoryScope.SHARED],
        "risk": [MemoryScope.PRIVATE, MemoryScope.SHARED],
        "analysis": [MemoryScope.SHARED],
        "business": [MemoryScope.PRIVATE],
        "aggregation": [MemoryScope.ORGANIZATION],
    }
    
    # Agent 可读作用域
    READ_PERMISSIONS = {
        "customer_service": [MemoryScope.PRIVATE, MemoryScope.SHARED, MemoryScope.ORGANIZATION],
        "advisor": [MemoryScope.PRIVATE, MemoryScope.SHARED, MemoryScope.ORGANIZATION],
        "risk": [MemoryScope.PRIVATE, MemoryScope.SHARED, MemoryScope.ORGANIZATION],
        "analysis": [MemoryScope.SHARED, MemoryScope.ORGANIZATION],
        "business": [MemoryScope.PRIVATE, MemoryScope.ORGANIZATION],
        "aggregation": [MemoryScope.ORGANIZATION],
    }
    
    def __init__(self, subject_scope_service):
        self.subject_scope_service = subject_scope_service
    
    async def can_write(
        self,
        agent: str,
        scope: MemoryScope,
        customer_id: Optional[str] = None
    ) -> bool:
        """检查 Agent 是否有权写入该作用域"""
        
        allowed = self.WRITE_PERMISSIONS.get(agent, [])
        if scope not in allowed:
            return False
        
        # shared 作用域需要客户授权
        if scope == MemoryScope.SHARED and customer_id:
            return await self.subject_scope_service.has_access(agent, customer_id)
        
        return True
    
    async def can_read(
        self,
        agent: str,
        scope: MemoryScope,
        customer_id: Optional[str] = None
    ) -> bool:
        """检查 Agent 是否有权读取该作用域"""
        
        allowed = self.READ_PERMISSIONS.get(agent, [])
        if scope not in allowed:
            return False
        
        # shared 作用域需要客户授权
        if scope in [MemoryScope.SHARED, MemoryScope.PRIVATE] and customer_id:
            return await self.subject_scope_service.has_access(agent, customer_id)
        
        return True
    
    def get_readable_scopes(self, agent: str) -> List[MemoryScope]:
        """获取 Agent 可读的所有作用域"""
        return self.READ_PERMISSIONS.get(agent, [])
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 7.3 与现有权限框架集成

python

```
# app/services/agent_capability.py（扩展）

class AgentCapabilityRegistry:
    # ... 现有代码 ...
    
    # 新增：Agent 的记忆权限
    MEMORY_PERMISSIONS = {
        "CustomerServiceAgent": {
            "write_scopes": ["private", "shared"],
            "read_scopes": ["private", "shared", "organization"],
        },
        "AdvisorAgent": {
            "write_scopes": ["private", "shared"],
            "read_scopes": ["private", "shared", "organization"],
        },
        "RiskAgent": {
            "write_scopes": ["private", "shared"],
            "read_scopes": ["private", "shared", "organization"],
        },
        "DataAnalysisAgent": {
            "write_scopes": ["shared"],
            "read_scopes": ["shared", "organization"],
        },
        "BusinessAgent": {
            "write_scopes": ["private"],
            "read_scopes": ["private", "organization"],
        },
    }
    
    @classmethod
    def can_access_memory(cls, agent: str, scope: str) -> bool:
        """检查 Agent 是否有权访问该作用域的记忆"""
        perms = cls.MEMORY_PERMISSIONS.get(agent, {})
        return scope in perms.get("read_scopes", [])
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 8. 实施路线图

### 8.1 分阶段实施





| 阶段        | 内容                              | 工期   | 依赖                     |
| ----------- | --------------------------------- | ------ | ------------------------ |
| **Phase 1** | 扩展 `CustomerMemory` 模型 + 迁移 | 1-2 天 | 现有 `CustomerMemory`    |
| **Phase 2** | 统一写入 API + 权限校验           | 2-3 天 | Phase 1                  |
| **Phase 3** | 统一检索 API + 向量检索集成       | 2-3 天 | Phase 2 + 现有知识库检索 |
| **Phase 4** | 聚合 Worker（离线洞察）           | 2-3 天 | Phase 3 + LLM            |
| **Phase 5** | 各 Agent 接入共享记忆             | 3-5 天 | Phase 4                  |

### 8.2 建议实施顺序

text

```
Week 1:
  ├── Phase 1: 数据模型扩展（1-2 天）
  └── Phase 2: 统一写入 API（2-3 天）

Week 2:
  ├── Phase 3: 统一检索 API（2-3 天）
  └── Phase 4: 聚合 Worker 基础版（2-3 天）

Week 3:
  ├── Phase 5: 各 Agent 逐步接入
  │   ├── Day 1: CustomerServiceAgent
  │   ├── Day 2: AdvisorAgent
  │   ├── Day 3: RiskAgent
  │   ├── Day 4: DataAnalysisAgent
  │   └── Day 5: BusinessAgent
  └── 联调 + 回归测试
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 8.3 各 Agent 接入优先级





| Agent                  | 接入理由                           | 收益               | 优先级 |
| ---------------------- | ---------------------------------- | ------------------ | ------ |
| `AdvisorAgent`         | 需要消费客服 Agent 的客户偏好观察  | 推荐更精准         | 🔴 高   |
| `RiskAgent`            | 需要消费客服 Agent 的投诉/问题观察 | 风险判断更全面     | 🔴 高   |
| `CustomerServiceAgent` | 需要消费 Risk Agent 的预警摘要     | 客服更了解风险状态 | 🟡 中   |
| `DataAnalysisAgent`    | 需要消费聚合洞察                   | 分析更有深度       | 🟢 低   |
| `BusinessAgent`        | 执行操作前检查相关记忆             | 操作更安全         | 🟢 低   |

## 9. 代码改动清单

### 9.1 Phase 1：数据模型扩展

text

```
修改文件：
  app/models/customer_memory.py          # 增加字段
  migrations/versions/0023_shared_memory_extensions.py  # 迁移脚本

新增文件：
  app/schemas/memory.py                  # Memory Schema
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 9.2 Phase 2：统一写入 API

text

```
新增文件：
  app/services/shared_memory_service.py  # 共享记忆服务
  app/services/memory_permission.py      # 权限校验
  app/api/shared_memory.py              # API 路由
  tests/test_shared_memory_write.py     # 写入单元测试
  scripts/test_shared_memory_http.py    # HTTP 回归测试
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 9.3 Phase 3：统一检索 API

text

```
修改文件：
  app/services/shared_memory_service.py  # 增加 search 方法
  app/repositories/memory.py             # 增加向量检索

新增文件：
  tests/test_shared_memory_search.py    # 检索单元测试
  scripts/test_shared_memory_search_http.py  # HTTP 回归测试
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 9.4 Phase 4：聚合 Worker

text

```
新增文件：
  workers/memory_aggregation_worker.py   # 聚合 Worker
  workers/aggregation_scheduler.py       # 调度器
  docker-compose.p0.yml                 # 增加 aggregation-worker
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 9.5 Phase 5：各 Agent 接入

text

```
修改文件：
  app/services/customer_agent_execution.py  # CustomerServiceAgent 接入
  app/services/advisor_agent.py             # AdvisorAgent 接入
  app/services/risk_agent.py                # RiskAgent 接入
  app/services/data_analysis_agent.py       # DataAnalysisAgent 接入
  app/services/business_agent.py            # BusinessAgent 接入
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 10. 验收标准

### 10.1 Phase 1 验收





| 测试场景 | 验收命令                                             | 预期结果         |
| -------- | ---------------------------------------------------- | ---------------- |
| 迁移执行 | `alembic upgrade head`                               | 新增字段创建成功 |
| 模型扩展 | `python -m py_compile app/models/customer_memory.py` | 编译通过         |
| 回填数据 | 查询 `customer_memory` 表                            | 历史记录有默认值 |

### 10.2 Phase 2 验收





| 测试场景     | 验收命令                                         | 预期结果               |
| ------------ | ------------------------------------------------ | ---------------------- |
| 写入私有记忆 | `POST /shared-memory/write` with `scope=private` | 201 Created            |
| 写入共享记忆 | `POST /shared-memory/write` with `scope=shared`  | 201 Created            |
| 越权写入     | 非授权 Agent 写 `shared`                         | 403 FORBIDDEN          |
| 冲突检测     | 同一主题高置信度覆盖低置信度                     | 旧记忆 `valid_to` 更新 |

### 10.3 Phase 3 验收





| 测试场景     | 验收命令                                         | 预期结果       |
| ------------ | ------------------------------------------------ | -------------- |
| 检索共享记忆 | `POST /shared-memory/search` with `scope=shared` | 返回共享记忆   |
| 检索私有记忆 | 非写入 Agent 检索 `private`                      | 403 FORBIDDEN  |
| 向量检索     | 自然语言查询                                     | 返回相关记忆   |
| 消费追踪     | 检索后查询 `consumed_by`                         | 包含当前 Agent |

### 10.4 Phase 4 验收





| 测试场景   | 验收命令                  | 预期结果           |
| ---------- | ------------------------- | ------------------ |
| 聚合执行   | 手动触发 Worker           | 生成组织级记忆     |
| 聚类正确性 | 检查聚类结果              | 同类观察聚合到一组 |
| 洞察质量   | 检查 LLM 输出             | 可读、有行动建议   |
| 组织级记忆 | 查询 `scope=organization` | 存在聚合结果       |

### 10.5 Phase 5 验收





| Agent                | 验收场景                   | 预期结果             |
| -------------------- | -------------------------- | -------------------- |
| CustomerServiceAgent | 检索 Risk Agent 的预警摘要 | 客服知道客户风险状态 |
| AdvisorAgent         | 检索客服 Agent 的客户偏好  | 推荐更精准           |
| RiskAgent            | 检索客服 Agent 的投诉记录  | 风险判断更全面       |
| DataAnalysisAgent    | 检索聚合洞察               | 分析有深度           |
| BusinessAgent        | 执行前检查相关记忆         | 操作前有上下文       |

## 11. 与现有文档的关联





| 文档                                          | 关联内容                            |
| --------------------------------------------- | ----------------------------------- |
| `docs/DEFENSE_SYSTEM_DESIGN_AND_EVOLUTION.md` | 补充共享记忆架构章节                |
| `docs/PLAN.md`                                | 新增 Phase 6：共享记忆实施          |
| `docs/ISSUES.md`                              | 新增 ISSUE-0403：多 Agent 共享记忆  |
| `docs/TESTS.md`                               | 新增 TEST-0406 ~ TEST-0410 验收记录 |
| `docs/AGENT_FUNCTIONS_AND_BUSINESS_DESIGN.md` | 补充记忆共享机制说明                |

## 12. 总结

### 12.1 核心设计原则

1. **安全第一**：共享记忆首先是安全问题，其次才是功能问题
2. **作用域标签是基础**：写入时打标签，读取时按标签组合过滤
3. **端到端权限校验**：写入时强校验 + 读取时软过滤，处处生效
4. **来源可追溯**：每条记忆有来源、有时间、有作用域
5. **渐进式演进**：私有 → 共享 → 组织级认知，不急于求成

### 12.2 与现有架构的关系

text

```
你已有的基础设施              本方案新增
─────────────────────────────────────────────────
CustomerMemory（模型）    →  扩展为共享记忆模型
PermissionScopedCache     →  复用为记忆读取缓存
AgentCapabilityRegistry   →  复用为记忆权限前置
SubjectScopeService       →  复用为客户数据范围
KnowledgeRetriever        →  复用为记忆检索引擎
AuditLog                  →  复用为记忆审计
AsyncTask + Outbox        →  复用为异步聚合
LangGraph Checkpointer    →  可接入记忆作为上下文
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 12.3 一句话概括

> **你已经有权限框架、缓存隔离、记忆存储、审计日志——缺的只是一层“Agent 间共享记忆的语义层”，让客服 Agent 的观察能被投顾 Agent 复用，让风险 Agent 的预警能被业务 Agent 感知。**