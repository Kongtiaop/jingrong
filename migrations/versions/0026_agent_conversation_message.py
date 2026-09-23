"""add agent_conversation_message for AI 问答留痕

背景：AI 阶段的问答内容此前**无处留存**——`audit_logs` 只有工具调用轨迹，
且 `output_preview` 仅存结构性摘要（如"命中 5 条知识块"），没有答案全文。
后果：刷新页面后对话即消失，用户既看不到历史，也无从回溯"当时问了什么、答了什么"。

本表覆盖 AI 阶段的每一次问答，与 `customer_service_message`（**仅转人工之后**使用）
互补，两者不要混用：
    - 转人工前 → agent_conversation_message（本表）
    - 转人工后 → customer_service_message

影响面：纯新增表，不改动任何既有表结构；downgrade 直接 drop，可安全回滚。
"""
from alembic import op
import sqlalchemy as sa


revision = "0026_agent_conversation_message"
down_revision = "0025_knowledge_bm25_key_field"
branch_labels = None
depends_on = None

TABLE = "agent_conversation_message"


def _has_table(conn) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename=:t"),
        {"t": TABLE}).fetchone())


def upgrade():
    conn = op.get_bind()
    if _has_table(conn):
        # 幂等：表已存在（例如手工建过）则跳过，避免迁移在半途失败
        print(f"[0026] {TABLE} 已存在，跳过")
        return

    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(128), nullable=False),
        sa.Column("customer_id", sa.String(36),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("overall_status", sa.String(32), nullable=True),
        sa.Column("route", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agent_conversation_message_conversation_id", TABLE, ["conversation_id"])
    op.create_index("ix_agent_conversation_message_customer_id", TABLE, ["customer_id"])
    op.create_index("ix_agent_conversation_message_trace_id", TABLE, ["trace_id"])
    op.create_index("ix_agent_conversation_message_created_at", TABLE, ["created_at"])
    op.create_index("ix_agent_conversation_message_conv_created", TABLE,
                    ["conversation_id", "created_at"])
    print(f"[0026] 已创建 {TABLE}")


def downgrade():
    conn = op.get_bind()
    if _has_table(conn):
        op.drop_table(TABLE)
        print(f"[0026] 已删除 {TABLE}")
