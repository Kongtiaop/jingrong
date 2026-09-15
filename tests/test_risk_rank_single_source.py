# -*- coding: utf-8 -*-
"""风险等级比对口径必须唯一（防止再次分叉）。

ISSUE-20260914-RISK-RANK-RULES-DIVERGED

背景：同一套"等级排名 + 比较"此前在 4 处各写一遍，**默认值已经不一致**：
  suitability_service.py      LEVEL / _level()      缺失默认 1
  trading_service.py          内联 dict             缺失默认 1（下单硬门禁）
  api/admin.py                内联 dict             **缺失默认 0**（据此统计 missing_risk）
  api/profile.py              内联 risk_order       产品缺失 99 / 上限缺失 0（fail-closed）
风险：将来在**回答侧**再写一份（如"你能买什么"的推导），就会出现
      "回答说能买、下单却被拒"这类口径不一致。
本文件固定：唯一权威实现在 suitability_service，其余位置引用它，且 fail-closed 语义保留。
"""
import inspect

from app.services.suitability_service import is_suitable, risk_rank


def test_risk_rank_normalizes_both_prefixes():
    """C1–C5（客户等级）与 R1–R5（通用表述）同域，均归一为 1–5。"""
    assert risk_rank("C1") == 1 and risk_rank("C5") == 5
    assert risk_rank("R1") == 1 and risk_rank("R5") == 5
    # 容忍大小写与空白（用户/上游数据不可控）
    assert risk_rank("c3") == 3
    assert risk_rank("  C4  ") == 4


def test_risk_rank_returns_zero_for_missing():
    """缺失/未知必须返回 0，调用方据此区分"缺失"与"最低等级"。"""
    for value in ("", None, "X9", "unknown"):
        assert risk_rank(value) == 0, f"{value!r} 应为 0（缺失），而非默认成某个等级"


def test_is_suitable_is_same_level_or_higher():
    """适当性口径：客户等级 >= 产品等级即可买（与下单门禁一致）。"""
    assert is_suitable("C3", "C3") is True
    assert is_suitable("C4", "C3") is True     # 更高等级可买低风险产品
    assert is_suitable("C2", "C3") is False    # 低等级不可买高风险产品
    assert is_suitable("C1", "C1") is True
    assert is_suitable("C5", "C5") is True


def test_is_suitable_treats_missing_customer_as_not_suitable():
    """客户等级缺失时必须判为不适当（fail-closed），不能放行。"""
    assert is_suitable(None, "C1") is False
    assert is_suitable("", "C3") is False


def test_trading_service_uses_shared_rule_not_inline_dict():
    """下单门禁不得再内联等级字典（那是分叉的根源）。"""
    from app.services import trading_service
    src = inspect.getsource(trading_service)
    assert "from app.services.suitability_service import is_suitable" in src, \
        "trading_service 应引用共享实现"
    assert '{"C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5}' not in src, \
        "不应再内联等级字典（ISSUE-20260914-RISK-RANK-RULES-DIVERGED）"


def test_admin_uses_shared_rule():
    """后台统计不得再内联等级字典。"""
    from app.api import admin
    src = inspect.getsource(admin)
    assert "from app.services.suitability_service import risk_rank" in src
    assert 'level_rank = {"C1": 1' not in src, "admin 不应再内联 level_rank"


def test_profile_authorization_keeps_fail_closed_defaults():
    """授权上限校验必须保留 fail-closed：产品等级缺失不能变成放行。

    此处原语义特殊（产品缺失→99、上限缺失→0），与别处的 `or 1` 不同；
    转换时若误用 `or 1` 会把未知产品放行，属安全回归。故显式固定。
    """
    from app.api import profile
    src = inspect.getsource(profile)
    assert "risk_rank(product.risk_level) or 99" in src, \
        "产品等级缺失应保持 fail-closed（99），不能被改成 or 1（fail-open）"
    assert "risk_rank(authorization.risk_level_ceiling)" in src
