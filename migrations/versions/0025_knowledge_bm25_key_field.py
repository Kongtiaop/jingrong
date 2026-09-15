"""rebuild knowledge_chunk bm25 index with key_field in column list

ISSUE-20260914-PGSEARCH-BM25-KEY-FIELD-BLOCKS-INSERT

问题：knowledge_chunk 上的 BM25 索引定义为
    USING bm25 (content) WITH (key_field = 'id')
即 key_field 指名的列 **只在 WITH 中声明、未出现在字段列表里**。
在 pg_search 0.25.6 下该形态会导致**任何 INSERT 都失败**：
    asyncpg.exceptions.InternalServerError: No key field defined.
（影响所有写入路径：上传接口、knowledge_worker、导入脚本）

实测依据（独立临时表上对照 4 种定义）：
    A bm25 (id, content) WITH (key_field='id')   建索引✓ 写入✓   <== 正确形态
    B bm25 (content)    WITH (key_field='id')   建索引✓ 写入✗   <== 旧形态（本迁移要修）
    C bm25 (id, content) 无 WITH                建索引✗
    D bm25 (content)    无 WITH                 建索引✗

结论：key_field 指名的列必须出现在字段列表 (id, content) 中。

注意：该索引此前**不在版本管理内**（enable_pg_search_bm25.sql 的建索引语句被整体注释、
migrations 与 init 脚本都没有创建它），所以才会与扩展版本脱节而无人察觉。
本迁移把它正式纳入版本管理。

幂等性：仅在 pg_search 扩展可用时执行；索引不存在则创建，存在则按正确形态重建。
若目标库没有 pg_search（例如回退到 pgvector 镜像），整个迁移安全跳过。
"""
from alembic import op
import sqlalchemy as sa


revision = "0025_knowledge_bm25_key_field"
down_revision = "0024_shared_memory"
branch_labels = None
depends_on = None

INDEX_NAME = "idx_knowledge_chunk_content_bm25"
# 正确形态：key_field 列必须出现在字段列表中
CORRECT_DEF = (
    f"CREATE INDEX {INDEX_NAME} ON knowledge_chunk "
    f"USING bm25 (id, content) WITH (key_field = 'id')"
)


def _has_pg_search(conn) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM pg_extension WHERE extname = 'pg_search'")).fetchone())


def _index_def(conn) -> str | None:
    row = conn.execute(sa.text(
        "SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
        {"n": INDEX_NAME}).fetchone()
    return str(row[0]) if row else None


def upgrade():
    conn = op.get_bind()
    if not _has_pg_search(conn):
        # 目标库没装 pg_search（如回退 pgvector 镜像）→ 跳过，不影响其它迁移
        print("[0025] pg_search 未安装，跳过 BM25 索引重建")
        return

    current = _index_def(conn)
    if current is not None and "(id, content)" in current:
        print(f"[0025] BM25 索引已是正确形态，跳过：{current}")
        return

    if current is not None:
        print(f"[0025] 检测到旧形态索引，将重建：{current}")
        op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
    op.execute(CORRECT_DEF)
    print(f"[0025] 已按正确形态创建 BM25 索引：{CORRECT_DEF}")


def downgrade():
    conn = op.get_bind()
    if not _has_pg_search(conn):
        return
    # 回到旧形态（注意：旧形态下写入会失败，仅用于回滚演示）
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
    op.execute(
        f"CREATE INDEX {INDEX_NAME} ON knowledge_chunk "
        f"USING bm25 (content) WITH (key_field = 'id')")
