"""hybrid intelligence persistence

Revision ID: 0002_hybrid_intelligence
Revises: 0001_core_pipeline
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_hybrid_intelligence"
down_revision = "0001_core_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("analysis_records", sa.Column("id", sa.Text, primary_key=True), sa.Column("conversation_id", sa.Text), sa.Column("symbol", sa.Text, nullable=False), sa.Column("fingerprint", sa.Text, nullable=False), sa.Column("payload_json", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False), if_not_exists=True)
    for name, foreign in (("analysis_evidence", "analysis_id"), ("llm_reasoning_records", "analysis_id"), ("validator_records", "analysis_id")):
        op.create_table(name, sa.Column("id", sa.Text, primary_key=True), sa.Column(foreign, sa.Text, nullable=False), sa.Column("payload_json", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False), if_not_exists=True)
    op.create_table("analysis_cache_entries", sa.Column("fingerprint", sa.Text, primary_key=True), sa.Column("analysis_id", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False), sa.Column("expires_at", sa.Text), if_not_exists=True)
    op.create_table("learning_records", sa.Column("id", sa.Text, primary_key=True), sa.Column("trade_id", sa.Text), sa.Column("analysis_id", sa.Text), sa.Column("payload_json", sa.Text, nullable=False), sa.Column("created_at", sa.Text, nullable=False), if_not_exists=True)
    op.create_index("idx_analysis_conversation", "analysis_records", ["conversation_id", "created_at"], if_not_exists=True)


def downgrade() -> None:
    for name in ("learning_records", "analysis_cache_entries", "validator_records", "llm_reasoning_records", "analysis_evidence", "analysis_records"): op.drop_table(name)
