# -*- coding: utf-8 -*-
"""导入期风险等级词汇校验：必须能真的拦住 R1-R5。

同时验证不会误伤（R&D、R10、普通英文词）。
"""
import sys

sys.path.insert(0, ".")

from scripts.import_customer_corpus import validate_risk_level_vocabulary as V


def test_rejects_r_levels():
    errs = V("货币基金风险等级 R1，债券基金 R2。", "x.md")
    assert errs, "R1/R2 未被拦下 —— 校验形同虚设"
    assert "R1" in errs[0] and "R2" in errs[0]


def test_rejects_range_writing():
    errs = V("结构性存款风险等级通常为 R2—R3。", "x.md")
    assert errs


def test_accepts_c_levels():
    assert V("货币基金风险等级 C1，债券基金 C2，股票基金 C4。", "x.md") == []


def test_accepts_mixed_valid_customers():
    """客户级与产品级都用 C：'C2 客户不能购买 C3 产品' 应通过。"""
    assert V("如 C2 客户不能购买 C3 产品，除非签署确认书。", "x.md") == []


def test_does_not_false_positive_on_r10_or_words():
    """不得误伤 R10、R&D、R2D2、英文单词里的 R。"""
    for text in ["产品编号 R10", "研发 R&D 投入", "型号 R2D2", "Return R5x"]:
        assert V(text, "x.md") == [], f"误伤：{text!r}"


def test_does_not_false_positive_on_r_before_digit_boundary():
    """`R5x` 不算（后面紧跟字母），但 `R5。` 算。"""
    assert V("型号 R5x", "x.md") == []
    assert V("等级 R5。", "x.md") != []


def test_reports_all_distinct_offenders():
    errs = V("R1 和 R2 和 R4", "x.md")
    assert errs
    for lv in ("R1", "R2", "R4"):
        assert lv in errs[0], f"{lv} 未被列出"
