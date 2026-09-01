# 企业级智能Agent系统架构设计全书

> **文档版本**：v2.0
> **更新日期**：2026-08-22
> **适用架构**：知识图谱 + RAG + Agent 三位一体
> **文档性质**：完整技术方案，含设计决策、接口定义、代码示例、运维体系

## 目录

- 第1章 系统总体架构
- 第2章 知识图谱的实时更新机制
- 第3章 智能路由规则设计（Graph-RAG-Router）
- 第4章 多轮对话与指代消解
- 第5章 用户纠错与局部查询重写
- 第6章 执行树的压缩与历史管理
- 第7章 执行树的可视化方案
- 第8章 多用户并发隔离方案
- 第9章 执行树持久化与反序列化优化
- 第10章 可观测性与运维体系（Observability）
- 第11章 核心设计口诀与总结
- 附录A 术语表
- 附录B 版本历史

# 第1章 系统总体架构

## 1.1 三大核心模块职责





| 模块                    | 职责                                             | 技术载体                                     |
| ----------------------- | ------------------------------------------------ | -------------------------------------------- |
| **知识图谱（Graph）**   | 存储业务实体、关系、属性，负责关系推理和约束检查 | Neo4j / 关系型DB + 图查询引擎                |
| **RAG（检索增强生成）** | 非结构化文档检索，补充细节描述和证据片段         | 向量数据库（Milvus/Pinecone）+ Embedding模型 |
| **Agent（智能体）**     | 拆解任务、路由决策、调用工具、整合结果返回用户   | LLM + 工具调用框架（LangChain/LlamaIndex）   |

## 1.2 模块间协作关系

text

```
用户Query
    │
    ▼
┌─────────────────────────────────────────────────┐
│  Agent（任务拆解 + 路由决策）                    │
│  ├── 路由到图谱 → 结构化查询 → 返回实体/关系    │
│  ├── 路由到RAG  → 向量检索    → 返回文档片段    │
│  └── 两者都调用 → 结果融合    → 返回完整答案    │
└─────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────┐
│  结果融合层（置信度评估 + 证据拼接）             │
└─────────────────────────────────────────────────┘
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 1.3 核心设计原则





| 原则           | 说明                                            |
| -------------- | ----------------------------------------------- |
| **最终一致性** | 图谱/RAG更新不追求强一致，采用异步收敛          |
| **查询驱动**   | 复杂计算（如图谱路径推导）按需触发，而非预计算  |
| **可观测优先** | 所有关键路径埋点，支持数据驱动调优              |
| **优雅降级**   | 单点故障时自动切换备选路径（图谱→RAG）          |
| **用户可控**   | 歧义/低信场景交还用户决策，不替用户做高风险选择 |

# 第2章 知识图谱的实时更新机制

## 2.1 核心原则

- **放弃“实时强一致性”，拥抱“最终一致性”**
- 采用 **“增量失效（Invalidation） + 懒计算（Lazy Evaluation）”** 策略

## 2.2 三层更新架构





| 层级                       | 存储介质           | 更新策略                                                     |
| -------------------------- | ------------------ | ------------------------------------------------------------ |
| **符号定义表**（实体节点） | PostgreSQL         | 软删除（标记失效，保留指纹），新定义插入时带版本号           |
| **调用关系边**             | Neo4j / 内存哈希表 | 不修改旧边，新增边带 `commit_id`，查询时按版本过滤           |
| **反向索引**（加速器）     | Redis（Hash结构）  | 文件/实体变更时，只删除旧符号Key，插入新符号Key（O(变更数)，毫秒级） |

## 2.3 更新流程

text

```
变更触发（Git Diff / 文件系统监控 / 业务操作）
    │
    ▼
解析变更集，生成"局部子图"
    │
    ▼
挂载到暂存区，打上 version = current_version
    │
    ▼
反向索引精准定位受影响的外部依赖 → 标记为"待验证（Stale）"
    │
    ▼
查询时按需修复（Repair-on-Read）：
    查到依赖文件 → 比对 file_hash
    不一致 → 触发AST重解析 → 新结果覆盖本次查询
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 2.4 版本指纹保留（向量库策略）





| 策略                     | 说明                             |
| ------------------------ | -------------------------------- |
| `file_hash + chunk_hash` | 判断文档块是否变更               |
| 旧块标记为失效（软删除） | 非物理删除，保留历史追溯能力     |
| 新块插入失败             | 自动回退旧块，保证服务可用性     |
| `fingerprint` 永久保留   | 确保跨版本Diff稳定，支持变化追溯 |

# 第3章 智能路由规则设计（Graph-RAG-Router）

## 3.1 路由决策矩阵（四象限）





| 问题类型                | 示例                             | 路由策略           | 执行顺序                                 |
| ----------------------- | -------------------------------- | ------------------ | ---------------------------------------- |
| **精确关系查询**        | “A部门的负责人是谁？”            | 图谱独占           | 图谱查询 → 直接返回                      |
| **模糊概念/文档检索**   | “公司的报销流程是什么？”         | RAG独占            | 向量检索 → 返回文档片段                  |
| **带约束的实体查找**    | “入职超3年且绩效为A的员工”       | 图谱优先 + RAG补充 | 图谱查实体列表 → RAG补全详情             |
| **复杂推理 + 证据引用** | “王经理团队去年是否有预算超支？” | 图谱推理 + RAG验证 | 图谱找关系路径 → RAG检索会议纪要作为证据 |

## 3.2 路由规则实现（决策树）

python

```
def route_query(user_query):
    # Step 1: 提取意图信号
    has_exact_entity = detect_entity(user_query)      # 是否提到具体人名/部门名
    has_constraint = detect_constraint(user_query)    # 是否有 >, <, =, 时间范围等
    has_relation_word = detect_relation_word(user_query)  # "负责""属于""依赖"
    has_how_to = detect_how_to(user_query)            # "如何""怎样""步骤"
    has_example = detect_example(user_query)          # "比如""案例""有没有类似"
    
    # Step 2: 决策树
    if has_exact_entity and has_relation_word and not has_how_to:
        return {"priority": "graph_only"}
    elif has_how_to or (has_example and not has_exact_entity):
        return {"priority": "rag_only"}
    elif has_constraint and has_exact_entity:
        return {"priority": "graph_first", "supplement": "rag"}
    elif has_relation_word and has_how_to:
        return {"priority": "graph_derive", "verify": "rag"}
    else:
        return {"priority": "both", "merge": "union"}
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 3.3 “两者都要”的融合策略





| 触发条件                | 执行策略                              | 融合逻辑                                               |
| ----------------------- | ------------------------------------- | ------------------------------------------------------ |
| **实体 + 模糊描述并存** | 图谱精准查 + RAG相关文档              | 图谱结果与RAG结果做交集，按置信度排序                  |
| **需要解释“为什么”**    | 图谱提供流程/关联人 + RAG提供原文     | 拼接成“谁/何时/什么流程 + 原文证据”                    |
| **实体存在歧义**        | 先RAG消歧 → 确定实体ID → 再图谱查关系 | RAG给出候选上下文，Agent让用户确认或自动选置信度最高者 |

## 3.4 性能兜底策略





| 场景                   | 兜底策略                                         |
| ---------------------- | ------------------------------------------------ |
| 图谱查询超时（>500ms） | 自动降级为RAG全文检索                            |
| 新关系类型未建索引     | 灰度标记，新查询走RAG兜底，待夜间批量重建        |
| 图谱服务不可用         | 快速失败（Fail-fast），100%切换RAG               |
| RAG服务不可用          | 仅返回图谱结构化结果，附带提示“文档检索暂不可用” |

## 3.5 置信度回路（Confidence Loop）升级

### 3.5.1 问题背景

原路由是“单次硬决策”，存在两个盲区：

1. 图谱查到了但置信度很低（如实体名模糊匹配得分0.6）→ 直接返回可能误导用户。
2. 用户纠错时无法回溯“为什么当时走了图谱而不是RAG”。

### 3.5.2 置信度驱动的降级/升级策略

python

```
def route_with_confidence(user_query):
    route_decision = route_query(user_query)
    
    if route_decision["priority"] in ["graph_only", "graph_first"]:
        graph_result = execute_graph_query(user_query)
        confidence = graph_result.get("confidence", 0.0)
        
        if confidence >= 0.85:
            return {"source": "graph", "result": graph_result, "confidence": confidence}
        elif 0.60 <= confidence < 0.85:
            rag_result = execute_rag_query(user_query)
            return {
                "source": "graph_supplemented",
                "result": merge_graph_rag(graph_result, rag_result),
                "confidence": confidence,
                "supplement_reason": f"图谱置信度{confidence:.2f}，补充RAG证据"
            }
        else:  # confidence < 0.60
            rag_result = execute_rag_query(user_query)
            return {
                "source": "rag_fallback",
                "result": rag_result,
                "confidence": rag_result.get("confidence", 0.0),
                "fallback_reason": f"图谱置信度{confidence:.2f}低于阈值，降级为RAG"
            }
    else:
        return execute_original_route(route_decision)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 3.5.3 置信度阈值对照表





| 置信度区间  | 路由策略           | 用户可见标识                       |
| ----------- | ------------------ | ---------------------------------- |
| ≥ 0.85      | 图谱独占（高信）   | 🟢 “精确匹配”                       |
| 0.70 ~ 0.85 | 图谱为主 + RAG补充 | 🟡 “已补充文档证据”                 |
| 0.60 ~ 0.70 | RAG为主 + 图谱参考 | 🟠 “基于文档推断”                   |
| < 0.60      | 纯RAG检索          | 🔴 “未找到精确匹配，以下为相关文档” |

### 3.5.4 路由决策记录到执行树

json

```
{
  "step_id": "route_001",
  "step_type": "router",
  "input": {"user_query": "李总手下的团队有哪些项目？"},
  "output": {
    "decision": "graph_supplemented",
    "graph_confidence": 0.72,
    "rag_confidence": 0.85,
    "final_source": "graph_supplemented",
    "reason": "图谱查到3个候选，置信度0.72，补充RAG文档描述"
  },
  "timestamp": "2026-08-22T14:30:01Z"
}
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

# 第4章 多轮对话与指代消解

## 4.1 实体栈（Recent Mention Stack）数据结构

json

```
{
  "session_id": "sess_xxx",
  "mention_stack": [
    {"mention": "李华", "entity_id": "emp_001", "type": "person", "time": "14:30:01"},
    {"mention": "AI平台项目", "entity_id": "proj_089", "type": "project", "time": "14:30:05"}
  ],
  "current_focus": "proj_089",
  "anchors": [
    {
      "trigger": "歧义确认",
      "user_choice": "李强(emp_002)",
      "rejected_candidates": ["李华(emp_001)", "李敏(emp_003)"],
      "timestamp": "14:30:03"
    }
  ]
}
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 4.2 规则消解（优先级从高到低）





| 用户表述           | 消解规则                               | 示例                          |
| ------------------ | -------------------------------------- | ----------------------------- |
| “第一个”、“第二个” | 取最近一次列表返回结果中对应序号的实体 | “第一个” → 列表第1项          |
| “他”、“她”、“它”   | 取栈中最近一个同类型实体               | “他” → 最近的人               |
| “这个”、“那个”     | 取 `current_focus`                     | “这个” → 当前焦点实体         |
| “刚才的”、“之前的” | 取栈中倒数第二个同类型实体             | “刚才那个” → 前一个实体       |
| 仅提及职位/部门    | 结合图谱，根据上下文匹配               | “技术总监” → 当前部门关联的人 |

## 4.3 三段式消歧策略（v2.0升级）

python

```
def resolve_with_disambiguation(user_query, mention_stack, vector_db):
    candidates = retrieve_candidates(user_query, mention_stack)
    if not candidates:
        return None
    
    best_score = candidates[0].score
    second_score = candidates[1].score if len(candidates) > 1 else 0.0
    
    if best_score >= 0.85:
        # 高置信度：直接消解，不打扰用户
        return candidates[0]
    
    elif 0.70 <= best_score < 0.85:
        # 中置信度：返回 Top-2 让用户选择
        return {
            "action": "ask_user",
            "candidates": candidates[:2],
            "prompt": f"您说的是以下哪一个？\n1. {candidates[0].name} ({candidates[0].description})\n2. {candidates[1].name} ({candidates[1].description})"
        }
    
    else:  # best_score < 0.70
        # 低置信度：返回 Top-3 让用户选择
        return {
            "action": "ask_user",
            "candidates": candidates[:3],
            "prompt": f"未找到精确匹配，您指的是以下哪一位？\n1. ...\n2. ...\n3. ..."
        }
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 4.4 锚点（Anchor）回写机制

用户做出选择后，记录锚点：

json

```
{
  "anchor": {
    "trigger": "歧义确认",
    "user_choice": "李强(emp_002)",
    "rejected_candidates": ["李华(emp_001)", "李敏(emp_003)"],
    "context": "用户明确选择了李强，排除了其他李姓领导"
  }
}
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

**锚点作用**：

- 后续出现“李总”时**优先匹配锚点中的 user_choice**
- 用户说“不是李强，是李华”时，直接替换选择，不重走消歧流程

python

```
def resolve_with_anchor(user_query, mention_stack):
    anchor = mention_stack.get_last_anchor()
    if anchor and anchor["user_choice"] in user_query:
        return anchor["user_choice"]
    return resolve_with_disambiguation(user_query, mention_stack)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 4.5 焦点转移管理





| 场景                   | 焦点更新策略                         |
| ---------------------- | ------------------------------------ |
| 用户明确提及新实体     | 新实体成为 `current_focus`，压入栈   |
| 用户使用指代词         | `current_focus` 不变，映射到当前焦点 |
| 用户说“那XXX呢”        | 新实体成为新焦点，旧焦点降为次焦点   |
| 用户说“回到刚才那个”   | 弹出当前焦点，恢复上一个焦点（LIFO） |
| 用户发起完全无关新话题 | 清空实体栈重新开始                   |

# 第5章 用户纠错与局部查询重写

## 5.1 执行树（Execution Tree）数据结构

json

```
{
  "execution_tree": {
    "nodes": [
      {
        "id": "step_001",
        "step_type": "graph_query",
        "label": "查所有李总",
        "input": {"keyword": "李总"},
        "output": {"candidates": ["李华", "李强", "李敏"]},
        "is_cached": true,
        "is_dirty": false,
        "execution_time_ms": 12,
        "depends_on": [],
        "consumed_by": ["step_002"]
      },
      {
        "id": "step_002",
        "step_type": "user_choice",
        "label": "选择李强",
        "input": {"choice": 2},
        "output": {"entity_id": "emp_002"},
        "is_cached": true,
        "is_dirty": false,
        "depends_on": ["step_001"],
        "consumed_by": ["step_003"]
      }
    ],
    "current_branch": "main",
    "forks": [],
    "history_summary": ""
  }
}
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 5.2 数据血缘图（依赖追踪）

每个Step记录：





| 字段            | 说明                                         |
| --------------- | -------------------------------------------- |
| `depends_on`    | 依赖哪些上游Step的输出                       |
| `consumed_by`   | 被哪些下游Step依赖                           |
| `input_sources` | 每个输入参数的来源（上游Step/用户输入/常量） |

json

```
{
  "step_id": "step_003",
  "input_sources": {
    "team_id": "step_002.output.selected_team",
    "year": "user_input.constant"
  }
}
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 5.3 局部重写流程

**场景**：用户说“不是李华，是李强”

text

```
1. 指代消解定位出错Step（Step 2）
    │
    ▼
2. 标记依赖链：Step 3、Step 4 为"脏（Dirty）"
    │
    ▼
3. 复用Step 1结果（不重查"李总"）
    │
    ▼
4. 修改Step 2输入：choice=1 → choice=2
    │
    ▼
5. 只重跑Step 3、Step 4
    │
    ▼
6. 返回新结果
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 5.4 脏节点传播算法

python

```
def mark_dirty(start_node, execution_graph):
    dirty_set = set()
    queue = [start_node]
    
    while queue:
        node = queue.pop()
        if node not in dirty_set:
            dirty_set.add(node)
            for downstream in node.consumed_by:
                queue.append(downstream)
    
    return dirty_set  # 只重跑这些节点
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 5.5 Step级缓存（Memoization）

python

```
def execute_with_cache(step):
    cache_key = hash(step.step_type + json.dumps(step.input))
    
    if cache_key in step_cache and not step.is_dirty:
        return step_cache[cache_key]
    
    result = call_tool(step.step_type, step.input)
    step_cache[cache_key] = result
    return result
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 5.6 分叉（Fork）语义支持（v2.0升级）

### 5.6.1 分叉场景

用户说：

> “如果换成产品部，结果会怎样？”
> “对比一下用李强和用李华的结果有什么不同？”

此时执行树需要支持**分叉（Fork）**。

### 5.6.2 分叉数据结构

json

```
{
  "nodes": [
    {
      "id": "step_002",
      "label": "选择领导",
      "type": "user_choice",
      "forks": [
        {
          "branch_id": "branch_A",
          "condition": "选择李强",
          "children": ["step_003_A", "step_004_A"]
        },
        {
          "branch_id": "branch_B",
          "condition": "选择李华",
          "children": ["step_003_B", "step_004_B"]
        }
      ]
    }
  ],
  "current_branch": "branch_A",
  "compare_mode": false
}
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 5.6.3 分叉点压缩策略

**原则**：分叉点（Fork Point）**永久保留，不折叠、不归档**。

python

```
def should_archive_node(node):
    if node.is_fork_point:
        return False
    if node.branch_id == current_branch:
        return False
    return is_stale(node)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

# 第6章 执行树的压缩与历史管理

## 6.1 问题背景

用户连续纠错N次后，执行树膨胀到50+节点，内存/Redis压力增大。

## 6.2 三种压缩策略

### 策略1：路径折叠（Path Collapsing）





| 触发条件            | 处理方式                                         |
| ------------------- | ------------------------------------------------ |
| 同一Step超过3个版本 | 保留当前路径（最新），历史版本压缩为“变异点摘要” |

python

```
def collapse_history(execution_tree, max_versions=3):
    if len(execution_tree.branches) > max_versions:
        latest = execution_tree.branches[-1]
        execution_tree.history_summary = generate_summary(execution_tree.branches[:-1])
        execution_tree.branches = [latest]
        clean_stale_cache(execution_tree.branches[:-1])
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 策略2：语义去重（Semantic Deduplication）





| 触发条件                       | 处理方式                   |
| ------------------------------ | -------------------------- |
| 两个不同输入的Step产出相同结果 | 合并为一个节点，重定向依赖 |

python

```
def deduplicate_steps(execution_tree):
    hash_map = {}
    for node in execution_tree.all_nodes():
        if node.output_hash in hash_map:
            redirect_dependencies(node, hash_map[node.output_hash])
            remove_node(node)
        else:
            hash_map[node.output_hash] = node
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 策略3：时间衰减归档（Time Decay Archiving）





| 触发条件              | 处理方式                  |
| --------------------- | ------------------------- |
| 节点超过5轮对话未访问 | 归档到数据库，从Redis移除 |

python

```
def archive_stale_nodes(execution_tree, max_rounds=5):
    current_round = get_current_dialogue_round()
    for node in execution_tree.all_nodes():
        if current_round - node.last_access_round > max_rounds:
            archive_to_db(node)
            execution_tree.remove(node)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 6.3 压缩层级决策表





| 纠错轮次  | 执行树规模                | 处理方式                |
| --------- | ------------------------- | ----------------------- |
| 第1-3次   | 完整DAG（所有分支可见）   | 不压缩                  |
| 第4-6次   | 折叠历史分支，保留摘要    | 路径折叠（保留3个版本） |
| 第7次以后 | 只保留当前路径 + 摘要文本 | 语义去重 + 时间归档     |

# 第7章 执行树的可视化方案

## 7.1 三种展示方式

### 方式1：Mermaid流程图





<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

**图例**：灰色=复用缓存，黄色=本次重跑，绿色=最终结果

### 方式2：Diff对比视图

text

```
用户: 把李强改成李华
Agent: 
  ✅ 已修改参数:
     - 实体: 李强(产品部) → 李华(研发部)
  
  🔄 受影响的重查步骤:
     - 查团队: 产品部 → 研发部 (重新执行)
  
  📊 结果变化:
     - 新增: AI平台
     - 移除: CRM系统
     - 保留: 数据中台
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 方式3：交互式执行树

- 每个节点可展开查看输入参数
- 显示“复用”/“重跑”标签
- 提供“回退到该版本”按钮
- 分叉场景支持“对比分支”和“切换分支”

## 7.2 后端输出JSON结构

json

```
{
  "execution_tree": {
    "nodes": [...],
    "diff_summary": {
      "changed_params": ["实体: 李强 → 李华"],
      "affected_steps": ["查团队", "查项目"],
      "result_changes": {
        "added": ["AI平台"],
        "removed": ["CRM系统"],
        "unchanged": ["数据中台"]
      }
    },
    "visualization": {
      "mermaid": "graph TD...",
      "legend": {"cached": "灰色", "dirty": "黄色", "result": "绿色"}
    }
  }
}
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

# 第8章 多用户并发隔离方案

## 8.1 三层隔离策略

### 第一层：会话级隔离（Session-Level）

python

```
# Redis Key设计
session_cache = {
    f"session:{session_id}:execution_tree": "...",
    f"session:{session_id}:mention_stack": "...",
    f"session:{session_id}:step_cache": "{...}",
    f"session:{session_id}:focus": "proj_089",
    f"session:{session_id}:anchors": "[...]"
}
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 第二层：查询结果级共享（跨会话）

python

```
query_cache = {
    f"query:graph:{query_hash}": {"result": "...", "version": "v3"},
    f"query:rag:{query_hash}": {"result": "...", "version": "v2"},
}
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

- 共享的是“裸数据”（不包含用户个性化上下文）
- 带版本号，写操作时自动递增版本号使缓存失效

### 第三层：资源配额（防止“坏邻居”）





| 资源类型       | 配额          | 实现方式     |
| -------------- | ------------- | ------------ |
| 图谱查询并发数 | 2/Session     | Semaphore    |
| RAG检索并发数  | 3/Session     | Semaphore    |
| 执行树节点数   | 50/Session    | 超出强制压缩 |
| Step缓存条目数 | 100/Session   | LRU淘汰      |
| 单次查询超时   | 图谱5s, RAG3s | 超时中断     |

python

```
class SessionResourceManager:
    def __init__(self, session_id):
        self.graph_semaphore = Semaphore(2)
        self.rag_semaphore = Semaphore(3)
        self.node_limit = 50
    
    async def execute_graph_query(self, query):
        async with self.graph_semaphore:
            return await call_graph_api(query, timeout=5)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 8.2 版本号缓存失效机制

python

```
global_version = {
    "graph:entity:emp_001": "v3",
    "graph:relation:dept": "v5",
}

def get_query_cache(query_hash, entity_id):
    cache_data = redis.get(f"query:cache:{query_hash}")
    if cache_data and cache_data["version"] == global_version.get(entity_id):
        return cache_data["result"]
    return None
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 8.3 图谱写操作的并发控制（v2.0升级）

### 8.3.1 读写分离





| 操作类型   | 并发控制策略                   | 锁粒度 |
| ---------- | ------------------------------ | ------ |
| **读操作** | 无锁，跨Session共享缓存        | N/A    |
| **写操作** | 全局队列 + 乐观锁（CAS版本号） | 实体级 |

### 8.3.2 乐观锁实现

python

```
def update_entity(entity_id, new_data):
    retry_count = 0
    while retry_count < 3:
        current_version = redis.get(f"graph:entity:{entity_id}:version")
        
        affected_rows = db.execute(
            "UPDATE entities SET data=? WHERE id=? AND version=?",
            new_data, entity_id, current_version
        )
        
        if affected_rows == 1:
            new_version = current_version + 1
            redis.set(f"graph:entity:{entity_id}:version", new_version)
            broadcast_version_change(entity_id, new_version)
            return {"success": True, "new_version": new_version}
        else:
            retry_count += 1
            time.sleep(0.01 * retry_count)
    
    return {"success": False, "error": "版本号冲突，请重试"}
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

### 8.3.3 版本号变更广播

python

```
def broadcast_version_change(entity_id, new_version):
    redis.publish("graph:version:changes", {
        "entity_id": entity_id,
        "new_version": new_version,
        "timestamp": time.now()
    })

# 每个Session订阅变更消息
def on_version_change(message):
    entity_id = message["entity_id"]
    new_version = message["new_version"]
    invalidate_query_cache_by_entity(entity_id, new_version)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

# 第9章 执行树持久化与反序列化优化

## 9.1 分片存储（三表分离）





| 表名                   | 存储内容                           | 查询频率 | 大小             |
| ---------------------- | ---------------------------------- | -------- | ---------------- |
| `execution_tree_meta`  | 元数据（根节点ID、创建时间、焦点） | 高       | 极小（<1KB）     |
| `execution_tree_nodes` | 节点详细信息（输入输出、依赖关系） | 中       | 中等（几十KB）   |
| `execution_tree_cache` | 节点缓存结果                       | 低       | 大（可能几百KB） |

## 9.2 二进制序列化（MessagePack）





| 格式             | 反序列化速度 | 数据大小     |
| ---------------- | ------------ | ------------ |
| JSON             | 基准（100%） | 基准（100%） |
| **MessagePack**  | **快 40%**   | **小 20%**   |
| Protocol Buffers | 快 60%       | 小 30%       |

**推荐**：MessagePack（速度与可维护性的折中）

python

```
import msgpack
binary_data = msgpack.packb(execution_tree_dict)
execution_tree = msgpack.unpackb(binary_data, raw=False)
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 9.3 增量存储（Delta Storage）





| 版本类型         | 存储内容                             | 策略           |
| ---------------- | ------------------------------------ | -------------- |
| 版本1（全量）    | 完整执行树                           | 基线快照       |
| 版本2-10（增量） | 只存变更 `{added, removed, changed}` | 增量链         |
| 每10个版本       | 生成新的全量快照                     | 防止增量链过长 |

python

```
def restore_version(snapshot_id):
    tree = load_snapshot(snapshot_id)
    deltas = load_deltas_after(snapshot_id)
    for delta in deltas:
        tree.apply_delta(delta)
    return tree
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 9.4 预热缓存（Cache Warming）

- 用户查看历史版本列表时，**后台异步加载最近3个版本**到Redis
- TTL = 10分钟
- 用户点击回退时直接命中Redis（1ms），不走数据库（50ms）

python

```
def list_history_versions(session_id):
    versions = db.query("SELECT * FROM tree_versions WHERE session_id=?", session_id)
    for ver in versions[-3:]:
        async_preload_to_redis(ver.id)
    return versions
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

## 9.5 性能优化效果预估





| 操作                   | 优化前       | 优化后         | 提升                |
| ---------------------- | ------------ | -------------- | ------------------- |
| 恢复历史版本（50节点） | 120ms        | 15ms           | **8倍**             |
| 缓存命中（热版本）     | 120ms        | 2ms            | **60倍**            |
| 100用户并发            | 偶发超时     | 稳定<200ms     | **可用性95%→99.9%** |
| 缓存一致性             | 手动清理易漏 | 版本号自动失效 | **一致性90%→100%**  |

# 第10章 可观测性与运维体系（Observability）

## 10.1 结构化日志规范

**每条关键操作记录以下字段**：

json

```
{
  "trace_id": "trace_20260822_143001_abc123",
  "step_id": "route_001",
  "session_id": "sess_xxx",
  "user_id": "user_001",
  "step_type": "router|resolve|graph_query|rag_query|rewrite|compress",
  "input": {...},
  "output": {...},
  "confidence": 0.72,
  "execution_time_ms": 45,
  "is_cached": false,
  "is_dirty": false,
  "version": "v2",
  "timestamp": "2026-08-22T14:30:01.123Z"
}
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

**日志存储**：Elasticsearch（全文检索）+ 对象存储（冷数据归档）。

## 10.2 关键指标看板





| 指标类别           | 具体指标             | 计算方式                               | 告警阈值            |
| ------------------ | -------------------- | -------------------------------------- | ------------------- |
| **路由准确率**     | 路由决策正确率       | 人工标注采样 / 用户纠错率反推          | < 85% 告警          |
| **消解成功率**     | 指代消解正确率       | 用户澄清后修正比例                     | < 80% 告警          |
| **缓存命中率**     | Step缓存命中率       | `cache_hit / (cache_hit + cache_miss)` | < 60% 告警          |
| **平均重跑节点数** | 每次纠错重跑的节点数 | `sum(重跑节点数) / 纠错次数`           | > 10 告警           |
| **图谱查询耗时**   | P95 / P99 延迟       | ES聚合统计                             | P95 > 2s 告警       |
| **RAG检索耗时**    | P95 / P99 延迟       | ES聚合统计                             | P95 > 1.5s 告警     |
| **端到端响应时间** | 用户可见的总耗时     | 从Query到Answer返回                    | P95 > 5s 告警       |
| **置信度分布**     | 各置信度区间占比     | 0-0.6 / 0.6-0.85 / 0.85-1.0            | 低信区间 > 30% 告警 |

## 10.3 数据驱动的调优闭环

text

```
日志采集 (ELK)
    │
    ▼
指标聚合 (Grafana/Prometheus)
    │
    ▼
识别瓶颈（如路由置信度分布异常）
    │
    ▼
调整阈值（如将RAG补充阈值从0.7调为0.75）
    │
    ▼
A/B测试验证
    │
    ▼
全量推送
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

**示例调优决策**：

- 发现“路由准确率”只有82%（低于85%告警线）
- 下钻发现：大量用户Query属于“带约束的实体查找”，但被错误路由到了“RAG独占”
- 调整路由规则：增强 `detect_constraint()` 的识别权重
- 两周后路由准确率提升至89%

## 10.4 结构化日志与执行树的关联

每个执行树节点都关联一个 `trace_id`，方便从用户反馈追溯到完整执行路径：

text

```
用户反馈"答案不对"
    │
    ▼
查询该session最后一条trace_id
    │
    ▼
拉取完整执行树
    │
    ▼
查看路由决策、置信度、重跑节点、缓存命中情况
    │
    ▼
定位到"图谱置信度0.62，降级为RAG，但RAG检索到了过时文档"
    │
    ▼
修复：更新文档版本号，强制刷新RAG索引
```

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _33882ae"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 12 12" fill="none" class="_9bc997d _28d7e84"><path d="M-5.24537e-07 0C-2.34843e-07 6.62742 5.37258 12 12 12L0 12L-5.24537e-07 0Z" fill="currentColor"></path></svg>

# 第11章 核心设计口诀与总结

## 11.1 完整架构能力矩阵





| 能力维度         | 实现方案                                 | 关键组件                   |
| ---------------- | ---------------------------------------- | -------------------------- |
| 知识图谱实时更新 | 增量失效 + 懒计算 + 反向索引             | Redis反向索引 + AST解析    |
| 智能路由         | 四象限决策矩阵 + 置信度回路              | GRR路由规则引擎            |
| 指代消解         | 实体栈 + 三段式消歧 + 锚点回写           | Mention Stack + Embedding  |
| 查询重写         | 执行树 + 脏标记传播 + Step缓存           | Execution Tree Manager     |
| 执行树分叉       | 分支语义 + 分叉点永久保留                | Fork-aware压缩引擎         |
| 历史压缩         | 路径折叠 + 语义去重 + 时间归档           | 压缩引擎（后台异步）       |
| 可视化           | Mermaid图 + Diff视图 + 交互式树          | 前端渲染 + 后端结构化JSON  |
| 并发隔离         | Session命名空间 + 资源配额 + 版本号缓存  | SessionManager + Semaphore |
| 图谱写锁         | 全局队列 + 乐观锁（CAS）+ 版本广播       | 版本号广播机制             |
| 持久化优化       | 三表分片 + MessagePack + 增量存储 + 预热 | 分片存储 + 预热调度器      |
| 可观测性         | 结构化日志 + 8大指标 + 调优闭环          | ELK + Grafana + Prometheus |

## 11.2 核心口诀

**路由决策**：

> 精确关系走图谱，模糊概念找RAG；
> 带约束的图优先，要推理的两手抓。
> 路由带置信，低了自动补；
> 决策记入树，纠错能追溯。

**指代消解**：

> 栈里记着谁来过，焦点标着谁在台；
> 规则听懂他/这个，向量消歧那个项目。
> 消歧分三段，用户来确认；
> 选完写锚点，下次优先用。

**纠错重写**：

> 改哪不重跑，跑哪不改上；
> 参数变了跑下游，实体换了跑分支。
> 执行树能分叉，对比两不误；
> 分叉点永保留，探索不迷路。

**压缩与可视化**：

> 版本不过三，多了就折叠；
> 缓存靠哈希，归档靠时间。
> 可视化三件套：图、表、可回退。

**并发与持久化**：

> 会话隔离用前缀，查询缓存带版本；
> 并发控制靠信号，超时熔断不能少。
> 图谱写加锁，版本广播全通知；
> 存树分片不存坨，序列化用MessagePack；
> 增量存储省空间，预热三版体验好。

**可观测性**：

> 日志全结构化，指标上大屏；
> 数据驱动调优，系统越跑越聪明。

# 附录A 术语表





| 术语           | 英文               | 说明                                             |
| -------------- | ------------------ | ------------------------------------------------ |
| **执行树**     | Execution Tree     | Agent执行任务的DAG结构，记录所有Step及依赖关系   |
| **脏节点**     | Dirty Node         | 因上游变更而需要重新执行的Step                   |
| **分叉**       | Fork               | 执行树从某节点分裂出多个并行分支，用于假设性探索 |
| **锚点**       | Anchor             | 用户确认后的消歧结果，用于后续快速匹配           |
| **置信度回路** | Confidence Loop    | 根据查询置信度动态调整路由策略的闭环机制         |
| **实体栈**     | Mention Stack      | 对话历史中所有被提及实体的栈结构                 |
| **当前焦点**   | Current Focus      | 最近一次对话中用户关注的主要实体                 |
| **增量存储**   | Delta Storage      | 仅存储执行树的变化部分，而非全量快照             |
| **版本广播**   | Version Broadcast  | 写操作后通知所有Session缓存失效的机制            |
| **血统图**     | Data Lineage Graph | 记录Step之间数据依赖关系的图谱                   |

# 附录B 版本历史





| 版本 | 日期       | 变更内容                                                     |
| ---- | ---------- | ------------------------------------------------------------ |
| v1.0 | 2026-08-22 | 初始版本：路由、消解、重写、压缩、可视化、并发、持久化       |
| v2.0 | 2026-08-22 | 新增：置信度回路、三段式消歧+锚点、分叉语义、图谱写锁、可观测性体系 |