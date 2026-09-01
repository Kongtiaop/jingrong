from scripts.check_rag_regression import _regressed


def test_no_regression_when_equal():
    base = {"hit_rate": 0.8, "recall": 0.6}
    assert _regressed(base, {"hit_rate": 0.8, "recall": 0.6}, 0.1, 0.1) is False


def test_regression_when_hit_drops_beyond_tolerance():
    base = {"hit_rate": 0.8, "recall": 0.6}
    assert _regressed(base, {"hit_rate": 0.5, "recall": 0.6}, 0.1, 0.1) is True


def test_regression_when_recall_drops_beyond_tolerance():
    base = {"hit_rate": 0.8, "recall": 0.6}
    assert _regressed(base, {"hit_rate": 0.8, "recall": 0.4}, 0.1, 0.1) is True


def test_tolerance_boundary_is_pass():
    # 恰好等于容差不算回归（> 而非 >=）
    base = {"hit_rate": 0.8, "recall": 0.6}
    assert _regressed(base, {"hit_rate": 0.7, "recall": 0.6}, 0.1, 0.1) is False
    assert _regressed(base, {"hit_rate": 0.8, "recall": 0.5}, 0.1, 0.1) is False


def test_both_within_tolerance_pass():
    base = {"hit_rate": 1.0, "recall": 0.9}
    assert _regressed(base, {"hit_rate": 0.9, "recall": 0.8}, 0.15, 0.15) is False
