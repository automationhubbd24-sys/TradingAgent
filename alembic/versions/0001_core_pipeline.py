"""core pipeline persistence schema

Revision ID: 0001_core_pipeline
Revises:
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_core_pipeline"
down_revision = None
branch_labels = None
depends_on = None

TABLES = {
    "conversations": [sa.Column("id", sa.Text, primary_key=True), sa.Column("title", sa.Text, nullable=False), sa.Column("active_symbol", sa.Text), sa.Column("created_at", sa.Text, nullable=False), sa.Column("updated_at", sa.Text, nullable=False)],
    "messages": [sa.Column("id", sa.Text, primary_key=True), sa.Column("conversation_id", sa.Text, sa.ForeignKey("conversations.id"), nullable=False), sa.Column("role", sa.Text, nullable=False), sa.Column("content", sa.Text, nullable=False), sa.Column("metadata_json", sa.Text, nullable=False, server_default="{}"), sa.Column("created_at", sa.Text, nullable=False)],
    "trade_decisions": [sa.Column("id", sa.Text, primary_key=True), sa.Column("conversation_id", sa.Text, sa.ForeignKey("conversations.id", ondelete="SET NULL")), sa.Column("symbol", sa.Text, nullable=False), sa.Column("direction", sa.Text, nullable=False), sa.Column("status", sa.Text, nullable=False), sa.Column("payload_json", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False)],
    "trade_snapshots": [sa.Column("id", sa.Text, primary_key=True), sa.Column("trade_id", sa.Text, nullable=False), sa.Column("payload_json", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False)],
    "trade_outcomes": [sa.Column("trade_id", sa.Text, primary_key=True), sa.Column("status", sa.Text, nullable=False), sa.Column("payload_json", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False), sa.Column("updated_at", sa.Text, nullable=False)],
    "post_mortems": [sa.Column("trade_id", sa.Text, primary_key=True), sa.Column("payload_json", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False)],
    "market_snapshots": [sa.Column("id", sa.Text, primary_key=True), sa.Column("symbol", sa.Text, nullable=False), sa.Column("payload_json", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False)],
}

def upgrade() -> None:
    for name, columns in TABLES.items(): op.create_table(name, *columns, if_not_exists=True)
    for name in ("lessons", "patterns", "candidate_rules"):
        op.create_table(name, sa.Column("id", sa.Text, primary_key=True), sa.Column("status", sa.Text, nullable=False), sa.Column("payload_json", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False), sa.Column("updated_at", sa.Text, nullable=False), if_not_exists=True)
    op.create_table("strategy_versions", sa.Column("id", sa.Text, primary_key=True), sa.Column("version", sa.Text, nullable=False), sa.Column("status", sa.Text, nullable=False), sa.Column("payload_json", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False), if_not_exists=True)
    op.create_table("audit_records", sa.Column("id", sa.Text, primary_key=True), sa.Column("event_type", sa.Text, nullable=False), sa.Column("payload_json", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False), if_not_exists=True)
    op.create_index("idx_messages_conversation", "messages", ["conversation_id", "created_at"], if_not_exists=True)
    op.create_index("idx_decisions_created", "trade_decisions", ["created_at"], if_not_exists=True)

def downgrade() -> None:
    for name in ("audit_records", "strategy_versions", "candidate_rules", "patterns", "lessons", "market_snapshots", "post_mortems", "trade_outcomes", "trade_snapshots", "trade_decisions", "messages", "conversations"): op.drop_table(name)
