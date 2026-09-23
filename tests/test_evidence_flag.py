# -*- coding: utf-8 -*-
"""证据状态标记：把「模型自己的判断」与「它恰好用到的词」解耦。

背景（2026-09-18 实测）：状态判定此前**完全依赖扫描答案文本里的关键词**
（`_INSUFFICIENT_MARKERS`）。用户问「公司客户规模」，答案已完整给出
注册用户 862万 / 有效客户 473万 等数据，却因结尾一句
「如需了解更详细的客户结构…当前引用资料未提供，建议联系人工客服」
命中 `未提供` 与 `资料未`，整条被判为 `partially_answered`，
界面上出现"需要补充信息 / 当前资料未完全覆盖该问题"。

——那句话说的是「你追问的附加信息没覆盖」，与「你问的问题没答上」是两回事。
执行层没有问题（工具调用成功、检索命中、数据完整），问题在**生成文本被当成状态判据**。
"""
from app.services.customer_rag_answer import (
    _INSUFFICIENT_MARKERS,
    answer_reports_insufficient,
    split_evidence_flag,
)


# ---------- 标记解析 ----------

def test_sufficient_flag_stripped_and_reported():
    answer, flagged = split_evidence_flag("注册用户数 862 万。\n\n[[evidence:sufficient]]")
    assert flagged is False
    assert answer == "注册用户数 862 万。", "标记行必须剥离——它是内部约定，不能让客户看到"
    assert "evidence" not in answer


def test_insufficient_flag_reported():
    answer, flagged = split_evidence_flag("抱歉，资料不足。\n[[evidence:insufficient]]")
    assert flagged is True
    assert answer == "抱歉，资料不足。"


def test_missing_flag_returns_none_for_fallback():
    answer, flagged = split_evidence_flag("普通回答，没有标记。")
    assert flagged is None, "未输出标记时必须返回 None，以便回退到关键词匹配"
    assert answer == "普通回答，没有标记。"


def test_flag_tolerates_case_and_spacing():
    for variant in ("[[EVIDENCE:SUFFICIENT]]", "[[ evidence : sufficient ]]",
                    "[[Evidence:Insufficient]]"):
        _, flagged = split_evidence_flag(variant)
        assert flagged is not None, f"该写法应能被识别：{variant}"


# ---------- 核心回归：完整答案 + 免责声明不得被判为资料不足 ----------

def test_complete_answer_with_disclaimer_is_not_insufficient():
    """本次修复要解决的原始问题。

    答案完整给出了数据，只是附带说明「更细的信息未提供」。
    旧逻辑会因 `未提供` / `资料未` 命中而判资料不足；
    现在模型显式标了 sufficient，就应以它的判断为准。
    """
    text = (
        "根据最新公开资料，公司注册用户数 862 万、有效客户数 473 万。\n"
        "如需了解更详细的客户结构（如个人/机构占比、地域分布等），"
        "当前引用资料未提供，建议联系人工客服进一步咨询。\n\n"
        "[[evidence:sufficient]]"
    )
    # 前提：旧的关键词匹配确实会误判（否则这条测试失去意义）
    assert answer_reports_insufficient(text) is True, "旧逻辑应误判——这正是要修的问题"

    answer, flagged = split_evidence_flag(text)
    assert flagged is False, "显式标记应覆盖关键词误判"
    assert "862" in answer, "正文必须完整保留"
    assert "[[evidence" not in answer, "标记不得残留"


def test_fallback_still_used_when_model_omits_flag():
    """模型漏标时保持旧行为——不能因为引入了新通道就让判定失守。"""
    answer, flagged = split_evidence_flag("当前引用资料未提供相关信息。")
    assert flagged is None
    assert answer_reports_insufficient(answer) is True


def test_fallback_markers_stay_broad():
    """回退通道的标记词必须保持宽口径。

    金融场景下**漏判比误判更糟**：误判只是让用户多看到一句提示，
    漏判会让"其实没答上"被标成已完成，用户以为问题解决了。
    故显式标记解决精度问题，回退通道维持保守——两者分工，不要顺手收窄它。
    """
    assert "资料不足" in _INSUFFICIENT_MARKERS
    assert "未提供" in _INSUFFICIENT_MARKERS
