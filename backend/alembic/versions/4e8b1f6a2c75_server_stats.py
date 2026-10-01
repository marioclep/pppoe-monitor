"""server_stats: host CPU, memory and disk, one row per minute

Revision ID: 4e8b1f6a2c75
Revises: 9a3f6c1d8e52
Create Date: 2026-09-28 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4e8b1f6a2c75"
down_revision: Union[str, None] = "9a3f6c1d8e52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "server_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cpu_percent", sa.Float(), nullable=False),
        sa.Column("mem_used_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mem_total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("disk_used_bytes", sa.BigInteger(), nullable=False),
        sa.Column("disk_total_bytes", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_server_stats_sampled_at", "server_stats", ["sampled_at"])


def downgrade() -> None:
    op.drop_index("ix_server_stats_sampled_at", table_name="server_stats")
    op.drop_table("server_stats")
