"""router_poll_stats: per-poll router totals for the dashboard history

Backfill:
- from traffic_samples: one row per (router, poll) -- a router's poll
  writes all its samples with the same sampled_at;
- from traffic_hourly: one row per (router, hour) for the whole hours
  *before* that router's first 5-minute sample (so no hour is counted from
  both sources), with clients_connected NULL (never stored).

Revision ID: 7c1e4a9b2d30
Revises: 5d2b7e9c41a0
Create Date: 2026-09-25 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7c1e4a9b2d30"
down_revision: Union[str, None] = "5d2b7e9c41a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "router_poll_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("router_id", sa.Integer(), sa.ForeignKey("routers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("polled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clients_connected", sa.Integer(), nullable=True),
        sa.Column("rx_bps", sa.BigInteger(), nullable=False),
        sa.Column("tx_bps", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_router_poll_stats_polled_at", "router_poll_stats", ["polled_at"])
    op.create_index("ix_router_poll_stats_router_time", "router_poll_stats", ["router_id", "polled_at"])

    op.execute(
        """
        INSERT INTO router_poll_stats (router_id, polled_at, clients_connected, rx_bps, tx_bps)
        SELECT c.router_id, s.sampled_at, COUNT(DISTINCT s.client_id), SUM(s.rx_bps), SUM(s.tx_bps)
        FROM traffic_samples s
        JOIN pppoe_clients c ON c.id = s.client_id
        GROUP BY c.router_id, s.sampled_at
        """
    )
    op.execute(
        """
        WITH first_sample AS (
            SELECT c.router_id, MIN(s.sampled_at) AS first_at
            FROM traffic_samples s
            JOIN pppoe_clients c ON c.id = s.client_id
            GROUP BY c.router_id
        )
        INSERT INTO router_poll_stats (router_id, polled_at, clients_connected, rx_bps, tx_bps)
        SELECT c.router_id, h.hour_start, NULL, SUM(h.rx_bytes) * 8 / 3600, SUM(h.tx_bytes) * 8 / 3600
        FROM traffic_hourly h
        JOIN pppoe_clients c ON c.id = h.client_id
        LEFT JOIN first_sample f ON f.router_id = c.router_id
        WHERE f.first_at IS NULL OR h.hour_start + INTERVAL '1 hour' <= f.first_at
        GROUP BY c.router_id, h.hour_start
        """
    )


def downgrade() -> None:
    op.drop_index("ix_router_poll_stats_router_time", table_name="router_poll_stats")
    op.drop_index("ix_router_poll_stats_polled_at", table_name="router_poll_stats")
    op.drop_table("router_poll_stats")
