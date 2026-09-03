# -*- coding: utf-8 -*-
"""pg_search BM25 中文拆词提取器单测（无需 DB）。"""
from app.repositories.knowledge import KnowledgeRepository as Repo


def _t(q):
    return Repo._extract_pg_search_terms(q)


def test_strips_stopword_prefix():
    # '什么是' 前缀被剥除，保留可召回的内容词
    out = _t("什么是适当性管理")
    assert "适当" in out or "适当性" in out or "适当性管理" in out
    # 不应再带纯疑问前缀 什么/是
    assert "什么" not in out


def test_short_phrase_kept_whole():
    # 2-4 字短语直接保留（pg_search 对连续中文子串可命中）
    out = _t("适当性")
    assert out == "适当性"


def test_empty_query_returns_empty():
    assert _t("") == ""
    assert _t("？？？") == ""


def test_alphanumeric_code_kept():
    # 纯编号/代码按原文保留
    assert _t("C1 产品") == "C1 产品" or "C1" in _t("C1 产品")


def test_no_stopword_only():
    # 纯疑问词 → 空
    assert _t("什么是") == ""
