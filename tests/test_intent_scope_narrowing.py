# -*- coding: utf-8 -*-
"""意图 scope 收窄：可收窄的收窄、语料为空的保持全开。

ISSUE-20260914-INTENT-EXPANSION-DOES-NOT-DIVERGE-RETRIEVAL

背景与决策依据（全部来自实验，非推断）：
  1. 此前所有公开知识意图共用同一个 6 类目 scope，导致不同意图检索**同一批文档**：
     实测 company_info vs company_business 的 chunk 集合 Jaccard=1.000（逐字节相同）。
  2. "改嵌入源"（向量腿改用各自 normalized 嵌入）能让意图分化（Jaccard 0.708->0.448），
     但会**系统性抬高相似度**：6 组里 5 组出现"门控口径 0.6366 / 实际返回 0.6828"，
     即从"落 judge 带"变为"直接放行"，**绕过门控**。用户明确要求不可接受。
  3. "收窄 scope"分化更彻底（Jaccard=0.000）且相似度**零虚高**（门控恒用原始 query）。
  4. 但收窄必须**按意图分别决定**（experiment_scope_narrowing_all_intents.py 实测）：
       company_info 语料49 / company_business 40 / faq 21 -> 收窄后命中=5，best 与现状逐位相等
       workflow      语料 0 -> 收窄后 **命中=0**
       risk_public   语料 0 -> 收窄后 **命中=0**
     故 workflow / risk_public **绝不能收窄**，否则直接答不出。
"""
from app.common.knowledge_scope import KnowledgeScope
from app.services.customer_rag_service import scope_for_customer_intent


def test_narrowable_intents_restrict_to_own_category():
    """语料充足的意图应收窄到自身类目（这正是'意图能区分'的来源）。"""
    for intent in ("company_info", "company_business", "faq"):
        scope = scope_for_customer_intent(intent)
        assert scope.categories == (intent,), \
            f"{intent} 应只检索自身类目，实际 {scope.categories}"


def test_empty_corpus_intents_stay_open():
    """语料为空的意图必须保持全开 —— 收窄会让它们检索为空、直接答不出。

    实测：workflow / risk_public 的类目下 0 个客户可见 chunk，
    收窄后命中数 = 0（现状为 5）。
    """
    wide = KnowledgeScope.public_customer()
    for intent in ("workflow", "risk_public"):
        scope = scope_for_customer_intent(intent)
        assert scope.categories == wide.categories, (
            f"{intent} 的类目语料为 0，必须保持全开；"
            f"实际被收窄为 {scope.categories} -> 会导致检索为空")


def test_narrowing_does_not_touch_permission_levels():
    """收窄只改类目，不得顺带改权限（权限收窄是另一回事，会误伤可见性）。"""
    wide = KnowledgeScope.public_customer()
    for intent in ("company_info", "company_business", "faq", "workflow", "risk_public"):
        scope = scope_for_customer_intent(intent)
        assert scope.permission_levels == wide.permission_levels, \
            f"{intent} 的权限被意外改动：{scope.permission_levels}"
        assert scope.knowledge_base_ids == wide.knowledge_base_ids


def test_product_recommendation_remains_empty_scope():
    """产品推荐不依赖知识库 RAG，保持空类目（原有行为，不得被本次改动影响）。"""
    scope = scope_for_customer_intent("product_recommendation")
    assert scope.categories == ()


def test_narrowed_scope_excludes_other_categories():
    """收窄后的 scope 必须真的排除其它类目（否则收窄无意义）。"""
    scope = scope_for_customer_intent("company_business")
    assert scope.allows(knowledge_base_id="x", category="company_business",
                        permission_level="public")
    assert not scope.allows(knowledge_base_id="x", category="company_info",
                            permission_level="public"), \
        "收窄后不应再允许其它类目（否则两个意图仍会检索同一批文档）"
