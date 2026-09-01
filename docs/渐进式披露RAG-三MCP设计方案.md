# 渐进式披露 RAG：三 MCP 设计方案（搜文件 / 看大纲 / 读文件）

> 版本：v1.0　日期：2026-08-28
> 目标：把"渐进式披露 RAG"落成三个受控 MCP（`file_search` / `file_outline` / `file_read`），核心难点是**"看大纲"（结构感知）**——生成带位置指针的文档层级，实现"按定位直读原文"。
> 关联：简历"渐进式披露 RAG"；方法论与 Claude Opus 5 记忆文件系统同源（先目录、按需打开、只读相关）；三个 MCP 用接口顺序强制"先搜→再感知→才能读"。

---

## 1. 背景与核心思想

> **渐进式披露**：先用最便宜的手段把候选缩到最小，再逐层打开，最后才读真正相关的内容。让模型永远不把无关内容读进上下文。

链路：
```
file_search（元数据/目录） → file_outline（结构/大纲） → file_read（正文片段）
  └─ 分库预筛（第一道过滤+权限）在前
```

三个 MCP 拆分的理由：**职责单一、接口顺序天然强制"先看目录再打开"**，比在提示词里写"先查目录再读"更可靠（呼应"能用接口结构保证，就别只靠提示词"）。

---

## 2. 三个 MCP 的接口设计

### MCP 1: `file_search`（搜文件 —— 只查元数据，不读正文）
```jsonc
{
  "tool": "file_search",
  "args": { "query": "string", "knowledge_base_id?": "string", "category?": "string", "top_k": 10 },
  "returns": [{
    "file_id": "string",
    "title": "string",
    "summary": "string",        // 一句话摘要，要有检索价值（决定要不要打开）
    "category": "string",
    "source": "string",          // 库/分类/权限
    "size_bytes": "int",
    "structured": "bool"         // 预标记是否有结构（供 file_outline 判断）
  }]
}
```
- 只在元数据层检索（标题/摘要/别名/分类），**不读正文**。
- 摘要质量是核心：写"订单服务的幂等和重试"比写"订单模块"有用——未来模型靠它判断要不要打开。

### MCP 2: `file_outline`（看大纲 —— 结构感知，返回带位置指针的层级）
```jsonc
{
  "tool": "file_outline",
  "args": { "file_id": "string" },
  "returns": {
    "file_id": "string",
    "status": "structured" | "flat" | "skip",
    "headings": [
      { "level": 1, "title": "string", "page": 12, "start_char": 12345, "char_len": 2000 }
    ],
    "reason": "string"           // status=skip 时说明（字数太少/无结构）
  }
}
```
- **status 语义**：
  - `structured`：可导航，`file_read` 用位置指针跳转。
  - `flat`：有文本但推断不出层级 → 走全文读取。
  - `skip`：字数太少/无结构 → 直接 `file_read` 全文，跳过导航。
- **位置指针用 page + start_char 双保险**（PDF 用页，纯文本用字符偏移）。

### MCP 3: `file_read`（读文件 —— 按定位直读正文片段）
```jsonc
{
  "tool": "file_read",
  "args": { "file_id": "string", "page?": "int", "start_char?": "int", "end_char?": "int", "heading?": "string" },
  "returns": { "content": "string", "source": "string", "page": "int", "start_char": "int" }
}
```

---

## 3. 核心：`file_outline` 怎么生成大纲

### 3.1 大纲的本质
大纲 = **章节层级 + 每章节的位置指针**。关键不是"有哪些标题"，而是**每个标题在第几页/哪个字符偏移**——这样 `file_read` 才能跳到对应位置读原文。没有位置指针的大纲只是目录，没法导航。

### 3.2 按文档类型的实现（优先级从高到低）

**① Markdown —— 正则解析标题（最简单）**
```python
import re
outline = []
for m in re.finditer(r'(?m)^(#{1,6})\s+(.*)$', text):
    outline.append({"level": len(m.group(1)), "title": m.group(2).strip(),
                    "start_char": m.start()})
```

**② DOCX —— 用标题样式**
```python
import docx
doc = docx.Document(path)
for i, p in enumerate(doc.paragraphs):
    style = p.style.name
    if style.startswith("Heading"):            # "Heading 1" 等
        outline.append({"level": int(style.split()[-1]), "title": p.text,
                        "paragraph_index": i})
```
> 前提是文档用了 Word"标题样式"；若用了大纲级别，用 `p.paragraph_format.outline_level` 更准。

**③ PDF 有内嵌书签（最简单最准）—— 直接读 outline**
```python
import fitz  # PyMuPDF
doc = fitz.open(path)
toc = doc.get_toc(simple=True)   # [[level, title, page], ...]
```
> Word/LaTeX 导出的 PDF 通常自带书签，一条命令拿到精确大纲+页码。

**④ PDF 无书签但有文本（生 PDF）—— 字体/字号推断标题**
```python
import fitz
doc = fitz.open(path)
for page in doc:
    d = page.get_text("dict")
    for block in d["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                text, size, flags = span["text"].strip(), span["size"], span["flags"]
                bold = flags & 16
                if text and (size > 正文阈值 or bold):
                    outline.append({"level": 按字号/加粗分级, "title": text,
                                    "page": page.number + 1})
```
> 核心 = "字号/加粗 > 正文 = 标题"，用阈值分级 H1>H2>H3。

**⑤ 扫描件 / 无结构 PDF —— OCR 后推断或 LLM 兜底**
- 扫描件：先用 **MinerU**（版面分析+OCR）输出结构化 Markdown，再走 ①。
- 实在没结构：把文档文本喂 LLM，返回结构化大纲 JSON（含位置/摘要）作为兜底。

### 3.3 处理顺序与兜底
```
Markdown标题 → DOCX标题样式 → PDF内嵌书签(get_toc) → 字体字号推断 → (扫描件)MinerU/OCR → LLM兜底
```
先试"免费且精确"的原生结构，解析不到才降级到推断/LLM。每类解析器输出统一 `{level, title, page, start_char, char_len}`。

---

## 4. 自适应规则（`file_outline` 何时跳过）

| 条件 | 行为 |
|---|---|
| 文档字数 < 阈值（如 2000） | `status=skip`，`file_read` 直接读全文，省一跳 |
| 推断不出层级（纯文本/表格堆叠） | `status=flat`，走全文读取 |
| 有清晰层级 | `status=structured`，按位置读片段 |

> 判断依据 = 字数/结构复杂度阈值，运行时动态决定，不是死板流程。

---

## 5. 安全与边界（沿用现有 harness 约束）

1. **分库预筛在前**：`file_search` 只在该问题对应的库/分类内搜（第一道过滤 + 权限边界）。
2. **只读强制**：三个 MCP 全部只读，不碰任何写操作。
3. **权限**：每个 MCP 校验 RBAC 权限码 + `KnowledgeScope`；`file_read` 返回内容前二次校验文件权限。
4. **溯源**：`file_read` 返回 source/page/start_char，答案绑定 文档→章节→页码，可审计。
5. **低置信/失败降级**：搜不到 → 澄清；读不到 → 转人工（复用 `customer_rag_handoff`）。

---

## 6. 落地优先级

| 阶段 | 内容 | 说明 |
|---|---|---|
| P0 | ① Markdown + ② DOCX + ③ PDF 书签 三类解析器 + `file_outline` 统一输出 | 覆盖大部分结构化制度/产品文档，最准、最好做 |
| P1 | ④ PDF 字体字号推断（无书签生 PDF） | 可选，扩展覆盖 |
| P2 | ⑤ MinerU/OCR（扫描件）+ LLM 兜底 | 成本高，作降级 |
| P3 | 三个 MCP 接入 Agent 执行链路 + 评测 | 验收命中率/引用正确率 |

---

## 7. 与 Opus 5 及现有系统的呼应

- **Opus 5**：其记忆文件系统用同一套"先目录、按需打开、只读相关"；本方案用于**文档检索**，方向不同、思路一致（简历"渐进式披露 RAG"的行业背书）。
- **现有系统**：复用 `KnowledgeScope`（分库预筛+权限）、`knowledge_document/version/chunk`（文件元数据 + 版本），`file_outline` 可从文档原始文件实时生成（不依赖预存 chunk）。
- **与 NL2SQL 同源**：本方案从"数据目录渐进式召回 Schema"推导而来——同一个"先目录、按需加载"原则。

---

## 8. 验收标准

- `file_search` 只在元数据层检索、不读正文；`file_outline` 返回带位置指针的层级；`file_read` 能按位置跳转直读原文。
- 短文档/无结构文档正确 `skip`/`flat`，不强制走导航。
- 分库/权限隔离生效：无权文件搜不到、读不到。
- 命中率/引用正确率/低置信转人工率在评测集上有数据。
