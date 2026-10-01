"""session_state per PPPoE session, traffic_hourly, raw_retention_days

- session_state goes from one row per client to one row per live PPPoE
  session, keyed by (router_id, interface_id). The old per-client state
  can't be split into sessions, so it is dropped: every session is first
  seen again on the next poll, losing one polling interval of traffic
  (accepted in the spec).
- traffic_hourly holds the per-hour rollup of traffic_samples.
- Seeds raw_retention_days = 7 (days of 5-minute samples kept).
- Adds an index on traffic_samples.sampled_at (rollup and purge filter on it
  alone).

traffic_samples and accumulation_periods are not touched otherwise.

Downgrading loses history older than raw_retention_days: the old code reads
charts only from traffic_samples, which purge has thinned; traffic_hourly is
dropped.

Revision ID: 5d2b7e9c41a0
Revises: ac6bc5da9228
Create Date: 2026-09-25 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5d2b7e9c41a0'
down_revision: Union[str, None] = 'ac6bc5da9228'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('traffic_hourly',
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('hour_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('rx_bytes', sa.BigInteger(), nullable=False),
    sa.Column('tx_bytes', sa.BigInteger(), nullable=False),
    sa.Column('peak_rx_bps', sa.BigInteger(), nullable=False),
    sa.Column('peak_tx_bps', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['pppoe_clients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('client_id', 'hour_start')
    )
    op.create_index('ix_traffic_hourly_hour_start', 'traffic_hourly', ['hour_start'], unique=False)

    op.create_index(
        'ix_traffic_samples_sampled_at', 'traffic_samples', ['sampled_at'], unique=False
    )

    op.drop_table('session_state')
    op.create_table('session_state',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('router_id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('interface_id', sa.String(length=32), nullable=False),
    sa.Column('interface_name', sa.String(length=128), nullable=False),
    sa.Column('last_uptime_seconds', sa.Integer(), nullable=False),
    sa.Column('last_rx_bytes', sa.BigInteger(), nullable=False),
    sa.Column('last_tx_bytes', sa.BigInteger(), nullable=False),
    sa.Column('last_poll_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_rx_bps', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_tx_bps', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['pppoe_clients.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['router_id'], ['routers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('router_id', 'interface_id', name='uq_session_state_router_interface')
    )
    op.create_index(op.f('ix_session_state_client_id'), 'session_state', ['client_id'], unique=False)

    op.execute(
        "INSERT INTO settings (key, value) VALUES ('raw_retention_days', '7') ON CONFLICT (key) DO NOTHING"
    )


def downgrade() -> None:
    # Back to one (empty) state row per client: every session is first seen
    # again on the next poll. The hourly rollup is dropped with its watermark.
    op.execute("DELETE FROM settings WHERE key IN ('raw_retention_days', 'hourly_rollup_until')")

    op.drop_index(op.f('ix_session_state_client_id'), table_name='session_state')
    op.drop_table('session_state')
    op.create_table('session_state',
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('last_uptime_seconds', sa.Integer(), nullable=False),
    sa.Column('last_rx_bytes', sa.BigInteger(), nullable=False),
    sa.Column('last_tx_bytes', sa.BigInteger(), nullable=False),
    sa.Column('last_poll_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_rx_bps', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_tx_bps', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['pppoe_clients.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('client_id')
    )

    op.drop_index('ix_traffic_hourly_hour_start', table_name='traffic_hourly')
    op.drop_table('traffic_hourly')

    # if_exists: this revision was applied to the dev DB before the index was
    # added to it in place, so the index may or may not be there.
    op.drop_index('ix_traffic_samples_sampled_at', table_name='traffic_samples', if_exists=True)
