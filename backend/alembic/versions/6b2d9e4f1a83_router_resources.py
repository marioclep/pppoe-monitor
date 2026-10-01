"""router resources: CPU/memory/disk per poll in router_poll_stats, and the
last model/version/uptime on routers

All nullable: older polls, and polls where /system/resource couldn't be
read, have none.

Revision ID: 6b2d9e4f1a83
Revises: 4e8b1f6a2c75
Create Date: 2026-09-28 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6b2d9e4f1a83"
down_revision: Union[str, None] = "4e8b1f6a2c75"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("router_poll_stats", sa.Column("cpu_load", sa.Integer(), nullable=True))
    op.add_column("router_poll_stats", sa.Column("mem_free_bytes", sa.BigInteger(), nullable=True))
    op.add_column("router_poll_stats", sa.Column("mem_total_bytes", sa.BigInteger(), nullable=True))
    op.add_column("router_poll_stats", sa.Column("hdd_free_bytes", sa.BigInteger(), nullable=True))
    op.add_column("router_poll_stats", sa.Column("hdd_total_bytes", sa.BigInteger(), nullable=True))
    op.add_column("routers", sa.Column("board_name", sa.String(128), nullable=True))
    op.add_column("routers", sa.Column("routeros_version", sa.String(64), nullable=True))
    op.add_column("routers", sa.Column("uptime_seconds", sa.BigInteger(), nullable=True))
    op.add_column("routers", sa.Column("resources_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("routers", "resources_at")
    op.drop_column("routers", "uptime_seconds")
    op.drop_column("routers", "routeros_version")
    op.drop_column("routers", "board_name")
    op.drop_column("router_poll_stats", "hdd_total_bytes")
    op.drop_column("router_poll_stats", "hdd_free_bytes")
    op.drop_column("router_poll_stats", "mem_total_bytes")
    op.drop_column("router_poll_stats", "mem_free_bytes")
    op.drop_column("router_poll_stats", "cpu_load")
