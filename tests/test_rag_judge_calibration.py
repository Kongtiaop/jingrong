from scripts.run_rag_judge_calibration import cohens_kappa, _as_binary


def test_kappa_perfect_agreement():
    assert cohens_kappa([1, 0, 1], [1, 0, 1]) == 1.0


def test_kappa_total_disagreement():
    assert cohens_kappa([1, 1, 0], [0, 0, 1]) <= 0.0


def test_kappa_all_same_prediction():
    # 两 raters 都全投 1 → 随机一致为 1，kappa 无信息量，约定返回 1.0
    assert cohens_kappa([1, 1, 1], [1, 1, 1]) == 1.0


def test_as_binary_threshold():
    assert _as_binary([{"x": 5}, {"x": 2}, {"x": 3}], "x", 3) == [1, 0, 1]


def test_kappa_length_mismatch():
    try:
        cohens_kappa([1, 0], [1, 0, 1])
        assert False, "should raise"
    except ValueError:
        pass
