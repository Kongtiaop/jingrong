# -*- coding: utf-8 -*-
"""fulltext_backend 配置校验 + 后端缺省契约。"""
import pytest

from app.core.settings import Settings


def _settings(**overrides) -> Settings:
    # 隔离环境：显式覆盖关键字段，避免依赖 .env；extra=ignore 保证无关字段无碍。
    base = dict(
        database_backend="postgresql",
        vector_store_backend="pgvector",
        database_write_backend="postgresql",
        embedding_dimension=1024,
        app_env="development",
        demo_accounts_enabled=True,
        dashscope_api_key="",
    )
    base.update(overrides)
    return Settings(**base)


def test_fulltext_backend_default_is_tsvector():
    s = _settings()
    assert s.fulltext_backend == "tsvector"


def test_fulltext_backend_accepts_pg_search():
    s = _settings(fulltext_backend="pg_search")
    assert s.fulltext_backend == "pg_search"
    s.validate_p0()  # 合法值不应抛错


def test_fulltext_backend_rejects_unknown():
    s = _settings(fulltext_backend="elasticsearch")
    with pytest.raises(ValueError):
        s.validate_p0()
