from app.services.knowledge_ingestion import _structure_chunks, chunk_text


def test_structure_chunks_preserves_heading_section():
    text = "# 公司信息\n\n## 一、基本信息\n\n公司全称 XX科技。\n\n## 二、牌照\n\n基金销售资格。"
    chunks = _structure_chunks(text, size=100)
    # 各标题节基本独立成块，不把"基本信息"和"牌照"揉进同一块
    joined = "\n\n".join(chunks)
    assert "## 一、基本信息" in joined
    assert "## 二、牌照" in joined
    # 语义单元（标题节）不该被截断成两个内容碎片
    assert any("## 一、基本信息" in c for c in chunks)
    assert any("## 二、牌照" in c for c in chunks)


def test_structure_chunks_keeps_table_together():
    text = "# 数据\n\n| 项目 | 值 |\n|------|---|\n| 客户数 | 1万 |\n\n正文段落。"
    chunks = _structure_chunks(text, size=1000)
    # 整张表作为一个 chunk（不拆散）
    assert any("| 客户数 |" in c and "| 项目 |" in c for c in chunks)


def test_structure_chunks_respects_size():
    text = "A" * 2000
    chunks = _structure_chunks(text, size=400)
    assert all(len(c) <= 400 for c in chunks)
    assert "".join(chunks) == text


def test_chunk_text_fixed_mode_backward_compat():
    text = "A" * 1000 + "\n" + "B" * 1000
    chunks = chunk_text(text, mode="fixed", size=400, overlap=40)
    assert chunks  # 滑动窗口产出多个块
    assert any("A" in c for c in chunks)
    assert any("B" in c for c in chunks)


def test_chunk_text_empty():
    assert chunk_text("   \n  ") == []
    assert _structure_chunks("") == []
