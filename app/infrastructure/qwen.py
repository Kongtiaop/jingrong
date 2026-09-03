from __future__ import annotations

import json
import re

import httpx
from openai import AsyncOpenAI

from app.core.settings import Settings
from app.common.logging.config import get_logger
from app.services.understanding_config import understanding_config


logger = get_logger(__name__)


# Function Calling 的结构化意图工具 Schema（与 LLMIntentFallbackService 的 StructuredIntent 对齐）
_INTENT_TEMPLATES = [
    "T1_INFORMATION_QUERY", "T2_PRODUCT_RECOMMENDATION", "T3_RECOMMEND_THEN_PURCHASE_DRAFT",
    "T4_PURCHASE_DRAFT", "T5_ORDER_STATUS", "T6_RISK_EXPLANATION", "T7_HUMAN_HANDOFF",
    "T8_MULTI_INTENT", "T9_MODIFY_DRAFT", "T10_CANCEL_DRAFT", "T0_UNKNOWN",
]
_TASK_TYPES = ["read_only_query", "recommendation", "trade_workflow", "human_handoff", "unknown"]

INTENT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "template": {"type": "string", "enum": _INTENT_TEMPLATES},
        "primary_intent": {"type": "string"},
        "secondary_intents": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "object", "additionalProperties": {"type": "object"}},
        "constraints": {"type": "array", "items": {"type": "string"}},
        "missing_required_fields": {"type": "array", "items": {"type": "string"}},
        "requires_clarification": {"type": "boolean"},
        "explicit_confirmation": {"type": "boolean"},
        "confidence": {"type": "number"},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string"},
                    "task_type": {"type": "string", "enum": _TASK_TYPES},
                    "template": {"type": "string", "enum": _INTENT_TEMPLATES},
                    "entities": {"type": "object", "additionalProperties": {"type": "object"}},
                    "constraints": {"type": "array", "items": {"type": "string"}},
                    "requires_clarification": {"type": "boolean"},
                    "confidence": {"type": "number"},
                },
                "required": ["intent", "task_type", "template"],
            },
        },
    },
    "required": ["template", "primary_intent", "confidence"],
}


def _rule_plan_signals(query: str) -> dict:
    """跑规则规划器取意图数/置信度，供难度模型路由使用。失败则回退默认。"""
    try:
        from app.services.customer_intent import CustomerIntentPlanner
        plan = CustomerIntentPlanner().plan(query or "")
        tasks = plan.tasks or []
        return {
            "intent_count": len(tasks),
            "rule_confidence": max((getattr(t, "confidence", 0) or 0) for t in tasks) if tasks else 0.0,
        }
    except Exception:  # noqa: BLE001 - 路由信号缺失不阻塞
        return {"intent_count": None, "rule_confidence": None}


def _repair_json(text: str) -> str:
    """Function Calling 的 arguments 不保证是合法 JSON（已实测会缺逗号/带尾逗号等）。

    做一个启发式修复，覆盖最常见失败模式；修不动就抛原样错误交由调用方重试/回退。
    """
    t = (text or "").strip()
    if t.startswith("```"):  # 偶尔带代码围栏
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    t = t.replace("True", "true").replace("False", "false").replace("None", "null")
    # 尾逗号：`"a":1,}` / `[1,]`
    t = re.sub(r",\s*([}\]])", r"\1", t)
    # 缺逗号：字符串值闭合引号后紧跟空白再出现 {、" 或 [  → 补逗号（合法 JSON 不会出现该形态）
    t = re.sub(r'"(\s+)(?=[{"\[])', '",\\1', t)
    return t


def _parse_intent_json(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001 - 尝试修复后重试
        return json.loads(_repair_json(text))


class QwenProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client = (
            AsyncOpenAI(
                api_key=settings.dashscope_api_key,
                base_url=settings.qwen_base_url,
                http_client=httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(120.0, connect=30.0)),
            )
            if settings.dashscope_api_key
            else None
        )

    async def check_config(self) -> dict[str, str]:
        if self._client is None:
            return {"status": "skipped", "reason": "DASHSCOPE_API_KEY is not configured"}
        return {"status": "configured", "model": self.settings.qwen_embedding_model}

    async def chat_completions(self, **kwargs):
        """Chat completions with a bounded interactive timeout.

        Each interactive call is capped by qwen_chat_*_timeout_seconds so a slow/
        flaky DashScope returns promptly (instead of hanging to the 120s httpx cap)
        and callers can degrade gracefully. Embedding keeps its own client timeout.
        """
        if self._client is None:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        timeout = kwargs.pop("timeout", None)
        if timeout is None:
            timeout = httpx.Timeout(
                self.settings.qwen_chat_timeout_seconds,
                connect=self.settings.qwen_chat_connect_timeout_seconds,
            )
        return await self._client.chat.completions.create(timeout=timeout, **kwargs)

    async def classify(self, *, query: str, conversation_summary: str = "") -> dict:
        """StructuredIntentProvider-compatible alias for intent fallback."""
        return await self.classify_intent(query=query, conversation_summary=conversation_summary)

    async def classify_intent(self, *, query: str, conversation_summary: str = "") -> dict:
        if self._client is None:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        system = (
            "你是财富管理客服意图解析器。只调用 extract_intent 输出结构化客户意图，不执行交易、不输出命令。"
            "交易意图只表达'需进入固定流程'，不得声称已确认交易，不得自动选产品。"
            "多意图必须拆成多个 tasks；推荐并交易拆成有依赖的 tasks。"
            "意图不明确或关键信息缺失时 requires_clarification=true 并列出 missing_required_fields。"
            "primary_intent 用规范码：order_status/workorder_status/asset_summary/product_status/product_recommendation/business_operation/risk_public/risk_explanation/risk_restriction/policy/faq/company_info/human_handoff。"
            "实体用 {value,certainty}，certainty∈explicit/approximate/inferred/missing。"
            "输出精简：无实体不写 entities，task 只需 intent/task_type/template，不要多余字段。\n"
            "示例：\n"
            "用户：'帮我查一下我的订单状态'\n"
            "extract_intent 参数：{\"template\":\"T5_ORDER_STATUS\",\"primary_intent\":\"order_status\",\"confidence\":0.95,\"requires_clarification\":false,\"tasks\":[{\"intent\":\"order_status\",\"task_type\":\"read_only_query\",\"template\":\"T5_ORDER_STATUS\"}]}\n"
            "用户：'推荐并申购稳健产品'\n"
            "extract_intent 参数：{\"template\":\"T3_RECOMMEND_THEN_PURCHASE_DRAFT\",\"primary_intent\":\"product_recommendation\",\"secondary_intents\":[\"business_operation\"],\"confidence\":0.9,\"requires_clarification\":true,\"missing_required_fields\":[\"product\",\"amount\"],\"tasks\":[{\"intent\":\"product_recommendation\",\"task_type\":\"recommendation\",\"template\":\"T2_PRODUCT_RECOMMENDATION\"},{\"intent\":\"business_operation\",\"task_type\":\"trade_workflow\",\"template\":\"T4_PURCHASE_DRAFT\"}]}\n"
            "用户：'先看看不要买，合适再说'\n"
            "extract_intent 参数：{\"template\":\"T2_PRODUCT_RECOMMENDATION\",\"primary_intent\":\"product_recommendation\",\"confidence\":0.85,\"requires_clarification\":false,\"tasks\":[{\"intent\":\"product_recommendation\",\"task_type\":\"recommendation\",\"template\":\"T2_PRODUCT_RECOMMENDATION\"}]}\n"
            "用户：'查一下订单然后看看我的风险'\n"
            "extract_intent 参数：{\"template\":\"T8_MULTI_INTENT\",\"primary_intent\":\"order_status\",\"secondary_intents\":[\"risk_restriction\"],\"confidence\":0.9,\"requires_clarification\":false,\"tasks\":[{\"intent\":\"order_status\",\"task_type\":\"read_only_query\",\"template\":\"T5_ORDER_STATUS\"},{\"intent\":\"risk_restriction\",\"task_type\":\"read_only_query\",\"template\":\"T6_RISK_EXPLANATION\"}]}\n"
            "风险类区分：\n"
            "用户：'查下我有没有交易限制'\n"
            "extract_intent 参数：{\"template\":\"T6_RISK_EXPLANATION\",\"primary_intent\":\"risk_restriction\",\"confidence\":0.85,\"requires_clarification\":true,\"tasks\":[{\"intent\":\"risk_restriction\",\"task_type\":\"read_only_query\",\"template\":\"T6_RISK_EXPLANATION\"}]}\n"
            "用户：'我的风险等级是多少'\n"
            "extract_intent 参数：{\"template\":\"T6_RISK_EXPLANATION\",\"primary_intent\":\"risk_public\",\"confidence\":0.9,\"requires_clarification\":false,\"tasks\":[{\"intent\":\"risk_public\",\"task_type\":\"read_only_query\",\"template\":\"T6_RISK_EXPLANATION\"}]}\n"
            "用户：'解释一下什么是风险等级'\n"
            "extract_intent 参数：{\"template\":\"T6_RISK_EXPLANATION\",\"primary_intent\":\"risk_explanation\",\"confidence\":0.9,\"requires_clarification\":false,\"tasks\":[{\"intent\":\"risk_explanation\",\"task_type\":\"read_only_query\",\"template\":\"T6_RISK_EXPLANATION\"}]}\n"
            "申购/赎回口语：\n"
            "用户：'我这个还能赎回不'\n"
            "extract_intent 参数：{\"template\":\"T4_PURCHASE_DRAFT\",\"primary_intent\":\"business_operation\",\"entities\":{\"side\":{\"value\":\"redeem\",\"certainty\":\"inferred\"}},\"confidence\":0.8,\"requires_clarification\":true,\"missing_required_fields\":[\"product\",\"amount\"],\"tasks\":[{\"intent\":\"business_operation\",\"task_type\":\"trade_workflow\",\"template\":\"T4_PURCHASE_DRAFT\"}]}\n"
            "用户：'我想买点稳健的，推荐下呗'\n"
            "extract_intent 参数：{\"template\":\"T3_RECOMMEND_THEN_PURCHASE_DRAFT\",\"primary_intent\":\"product_recommendation\",\"secondary_intents\":[\"business_operation\"],\"confidence\":0.85,\"requires_clarification\":true,\"missing_required_fields\":[\"product\",\"amount\"],\"tasks\":[{\"intent\":\"product_recommendation\",\"task_type\":\"recommendation\",\"template\":\"T2_PRODUCT_RECOMMENDATION\"},{\"intent\":\"business_operation\",\"task_type\":\"trade_workflow\",\"template\":\"T4_PURCHASE_DRAFT\"}]}\n"
            "常见语义映射：\n"
            "用户：'客服电话是多少'\n"
            "extract_intent 参数：{\"primary_intent\":\"faq\",\"template\":\"T1_INFORMATION_QUERY\",\"confidence\":0.9,\"requires_clarification\":false}\n"
            "用户：'营业时间是几点'\n"
            "extract_intent 参数：{\"primary_intent\":\"faq\",\"template\":\"T1_INFORMATION_QUERY\",\"confidence\":0.9,\"requires_clarification\":false}\n"
            "用户：'怎么开户'\n"
            "extract_intent 参数：{\"primary_intent\":\"workflow\",\"template\":\"T1_INFORMATION_QUERY\",\"confidence\":0.9,\"requires_clarification\":false}\n"
            "用户：'适当性是什么意思'\n"
            "extract_intent 参数：{\"primary_intent\":\"policy\",\"template\":\"T1_INFORMATION_QUERY\",\"confidence\":0.9,\"requires_clarification\":false}\n"
            "用户：'它安全吗'\n"
            "extract_intent 参数：{\"primary_intent\":\"product_status\",\"template\":\"T1_INFORMATION_QUERY\",\"confidence\":0.85,\"requires_clarification\":true}\n"
            "用户：'那个理财咋样'\n"
            "extract_intent 参数：{\"primary_intent\":\"product_status\",\"template\":\"T1_INFORMATION_QUERY\",\"confidence\":0.85,\"requires_clarification\":true}\n"
            "用户：'我不赎回，就查下收益'\n"
            "extract_intent 参数：{\"primary_intent\":\"asset_summary\",\"template\":\"T1_INFORMATION_QUERY\",\"confidence\":0.9,\"requires_clarification\":false}\n"
            "用户：'这个政策我没看懂，有人解释下吗'\n"
            "extract_intent 参数：{\"primary_intent\":\"policy\",\"secondary_intents\":[\"human_handoff\"],\"template\":\"T8_MULTI_INTENT\",\"confidence\":0.9,\"requires_clarification\":false,\"tasks\":[{\"intent\":\"policy\",\"task_type\":\"read_only_query\",\"template\":\"T1_INFORMATION_QUERY\"},{\"intent\":\"human_handoff\",\"task_type\":\"human_handoff\",\"template\":\"T7_HUMAN_HANDOFF\"}]}\n"
            "用户：'看下风险再推荐个稳健的'\n"
            "extract_intent 参数：{\"primary_intent\":\"risk_public\",\"secondary_intents\":[\"product_recommendation\"],\"template\":\"T8_MULTI_INTENT\",\"confidence\":0.9,\"requires_clarification\":false,\"tasks\":[{\"intent\":\"risk_public\",\"task_type\":\"read_only_query\",\"template\":\"T6_RISK_EXPLANATION\"},{\"intent\":\"product_recommendation\",\"task_type\":\"recommendation\",\"template\":\"T2_PRODUCT_RECOMMENDATION\"}]}\n"
            "用户：'帮我看看持仓和最近成交'\n"
            "extract_intent 参数：{\"primary_intent\":\"asset_summary\",\"secondary_intents\":[\"order_status\"],\"template\":\"T8_MULTI_INTENT\",\"confidence\":0.9,\"requires_clarification\":false,\"tasks\":[{\"intent\":\"asset_summary\",\"task_type\":\"read_only_query\",\"template\":\"T1_INFORMATION_QUERY\"},{\"intent\":\"order_status\",\"task_type\":\"read_only_query\",\"template\":\"T5_ORDER_STATUS\"}]}\n"
            "用户：'这个能买吗'\n"
            "extract_intent 参数：{\"primary_intent\":\"business_operation\",\"template\":\"T4_PURCHASE_DRAFT\",\"confidence\":0.8,\"requires_clarification\":true}\n"
            "用户：'我的钱还能提出来不'\n"
            "extract_intent 参数：{\"primary_intent\":\"asset_summary\",\"template\":\"T1_INFORMATION_QUERY\",\"confidence\":0.85,\"requires_clarification\":true}\n"
        )
        # 追加理解层配置的 few-shot（模型可自调，运行时加载；灰度按 query 分流）
        cfg_prompt = understanding_config.get_intent_prompt(query)
        for fs in cfg_prompt.get("few_shot", []):
            if isinstance(fs, dict) and fs.get("query") and fs.get("output"):
                system += f"用户：'{fs['query']}'\nextract_intent 参数：{fs['output']}\n"
        user = f"conversation_summary={conversation_summary}\nquery={query}"
        # 难度模型路由：先跑规则规划器取意图数/置信度（多意图/对抗才路由到大模型）
        rule_signals = _rule_plan_signals(query)
        model = understanding_config.model_for_query(
            query,
            rule_confidence=rule_signals["rule_confidence"],
            intent_count=rule_signals["intent_count"],
        )
        response = await self.chat_completions(
            model=model,
            temperature=0,
            max_tokens=2048,
            tools=[{"type": "function", "function": {"name": "extract_intent", "description": "从用户话术中提取结构化的财富管理意图", "parameters": INTENT_TOOL_SCHEMA}}],
            tool_choice={"type": "function", "function": {"name": "extract_intent"}},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        message = response.choices[0].message
        raw = None
        if message.tool_calls and message.tool_calls[0].function.arguments:
            raw = message.tool_calls[0].function.arguments
        elif message.content:
            raw = message.content
        if raw:
            try:
                return _parse_intent_json(raw)
            except Exception:  # noqa: BLE001 - 首次解析失败，带加固提示重试一次
                logger.warning("intent_json_parse_failed finish_reason=%s len=%s raw_head=%r",
                               getattr(message, "finish_reason", None), len(raw), raw[:120])
        # 重试一次：追加"只输出合法 JSON"加固指令（Function Calling 的 arguments 不保证合法）
        if raw:
            try:
                hardened_user = f"{user}\n【重要】必须只输出一个合法 JSON 对象。逗号、引号、大中小括号必须完整闭合，字符串值用双引号，不要省略、不要缺逗号、不要尾逗号。"
                response = await self.chat_completions(
                    model=model,
                    temperature=0,
                    max_tokens=2048,
                    tools=[{"type": "function", "function": {"name": "extract_intent", "description": "从用户话术中提取结构化的财富管理意图", "parameters": INTENT_TOOL_SCHEMA}}],
                    tool_choice={"type": "function", "function": {"name": "extract_intent"}},
                    messages=[{"role": "system", "content": system}, {"role": "user", "content": hardened_user}],
                )
                message = response.choices[0].message
                raw2 = message.tool_calls[0].function.arguments if (message.tool_calls and message.tool_calls[0].function.arguments) else message.content
                if raw2:
                    return _parse_intent_json(raw2)
            except Exception as exc:  # noqa: BLE001
                logger.warning("intent_retry_failed finish_reason=%s len=%s error_type=%s",
                               getattr(message, "finish_reason", None), len(raw2 or ""), type(exc).__name__)
            # 兜底：解析失败不应让意图层崩溃。返回规则回退信号，由
            # LLMIntentFallbackService 走规则规划器兜底（比 unknown 更准）。
            logger.warning("intent_json_unparseable_final finish_reason=%s len=%s raw_head=%r",
                           getattr(message, "finish_reason", None), len(raw[:200]), raw[:200])
            return {"template": "T0_UNKNOWN", "primary_intent": "unknown", "confidence": 0.1,
                    "requires_clarification": True, "source": "rules_fallback_needed"}
        raise ValueError("empty structured intent response")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._client is None:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        # DashScope may reset connections for the OpenAI-compatible endpoint
        # when a batch contains multiple inputs. Send one text per request and
        # preserve ordering; this is slower but deterministic for ingestion.
        vectors: list[list[float]] = []
        logger.info("embedding_started model=%s text_count=%s", self.settings.qwen_embedding_model, len(texts))
        for text in texts:
            response = None
            last_error: Exception | None = None
            for attempt in range(1, 4):
                try:
                    response = await self._client.embeddings.create(
                        model=self.settings.qwen_embedding_model,
                        input=text,
                        dimensions=self.settings.embedding_dimension,
                    )
                    break
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.warning("embedding_retry attempt=%s error_type=%s", attempt, type(exc).__name__)
            if response is None:
                raise last_error or RuntimeError("embedding request failed")
            vectors.append(response.data[0].embedding)
        logger.info("embedding_completed model=%s text_count=%s dimension=%s", self.settings.qwen_embedding_model, len(vectors), len(vectors[0]) if vectors else 0)
        if any(len(vector) != self.settings.embedding_dimension for vector in vectors):
            raise ValueError(f"embedding dimension must be {self.settings.embedding_dimension}")
        return vectors

    async def complete(self, *, system: str, user: str, model: str | None = None, temperature: float = 0.3, max_tokens: int = 1600) -> str:
        """通用受控文本生成（供只读投顾回复等场景）。

        调用方负责越权/合规/护栏；这里只做一次聊天补全并返回纯文本。
        """
        if self._client is None:
            raise RuntimeError("DASHSCOPE_API_KEY is not configured")
        model = model or self.settings.qwen_chat_model
        response = await self.chat_completions(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return (response.choices[0].message.content or "").strip()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
