"""add unique indexes, router last_polled_at and session bps

- Dedupes alert_thresholds (lowest id wins per client_id, and a single
  global row), repointing alert_events of the removed duplicates to the kept
  row so no event history is lost, then enforces uniqueness with partial
  unique indexes.
- Merges duplicate open accumulation periods per client into the lowest-id
  one (summing totals, keeping the earliest start), then enforces a single
  open period per client with a partial unique index.
- Removes settings rows holding the literal string 'None' (written by the
  old PUT /settings when the UI sent null for an empty field).
- Adds routers.last_polled_at and session_state.last_rx_bps/last_tx_bps.

Revision ID: ac6bc5da9228
Revises: 73840b49aa17
Create Date: 2026-09-23 15:23:48.913403

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ac6bc5da9228'
down_revision: Union[str, None] = '73840b49aa17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dedupe_alert_thresholds() -> None:
    # keeper per group: per client_id, and one group for all global rows
    # (client_id IS NULL -> COALESCE key -1, never a real serial id).
    op.execute(
        """
        CREATE TEMPORARY TABLE _threshold_remap ON COMMIT DROP AS
        SELECT id AS old_id,
               MIN(id) OVER (PARTITION BY COALESCE(client_id, -1)) AS keep_id
        FROM alert_thresholds
        """
    )
    op.execute(
        """
        UPDATE alert_events e
        SET threshold_id = r.keep_id
        FROM _threshold_remap r
        WHERE e.threshold_id = r.old_id AND r.old_id <> r.keep_id
        """
    )
    op.execute(
        """
        DELETE FROM alert_thresholds t
        USING _threshold_remap r
        WHERE t.id = r.old_id AND r.old_id <> r.keep_id
        """
    )


def _merge_duplicate_open_periods() -> None:
    op.execute(
        """
        CREATE TEMPORARY TABLE _open_period_groups ON COMMIT DROP AS
        SELECT client_id,
               MIN(id) AS keep_id,
               MIN(period_start) AS min_start,
               SUM(rx_bytes_total) AS rx_sum,
               SUM(tx_bytes_total) AS tx_sum
        FROM accumulation_periods
        WHERE period_end IS NULL
        GROUP BY client_id
        HAVING COUNT(*) > 1
        """
    )
    op.execute(
        """
        UPDATE accumulation_periods p
        SET period_start = g.min_start, rx_bytes_total = g.rx_sum, tx_bytes_total = g.tx_sum
        FROM _open_period_groups g
        WHERE p.id = g.keep_id
        """
    )
    op.execute(
        """
        DELETE FROM accumulation_periods p
        USING _open_period_groups g
        WHERE p.client_id = g.client_id AND p.period_end IS NULL AND p.id <> g.keep_id
        """
    )


def upgrade() -> None:
    _dedupe_alert_thresholds()
    _merge_duplicate_open_periods()
    op.execute("DELETE FROM settings WHERE value = 'None'")

    op.create_index('uq_accum_one_open_period_per_client', 'accumulation_periods', ['client_id'], unique=True, postgresql_where=sa.text('period_end IS NULL'))
    op.create_index('uq_alert_thresholds_client_id', 'alert_thresholds', ['client_id'], unique=True, postgresql_where=sa.text('client_id IS NOT NULL'))
    op.create_index('uq_alert_thresholds_single_global', 'alert_thresholds', [sa.text('(client_id IS NULL)')], unique=True, postgresql_where=sa.text('client_id IS NULL'))
    op.add_column('routers', sa.Column('last_polled_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('session_state', sa.Column('last_rx_bps', sa.BigInteger(), server_default=sa.text('0'), nullable=False))
    op.add_column('session_state', sa.Column('last_tx_bps', sa.BigInteger(), server_default=sa.text('0'), nullable=False))


def downgrade() -> None:
    # The data dedupe/merge steps are not reversed (the removed rows were
    # duplicates); only the schema changes are undone.
    op.drop_column('session_state', 'last_tx_bps')
    op.drop_column('session_state', 'last_rx_bps')
    op.drop_column('routers', 'last_polled_at')
    op.drop_index('uq_alert_thresholds_single_global', table_name='alert_thresholds', postgresql_where=sa.text('client_id IS NULL'))
    op.drop_index('uq_alert_thresholds_client_id', table_name='alert_thresholds', postgresql_where=sa.text('client_id IS NOT NULL'))
    op.drop_index('uq_accum_one_open_period_per_client', table_name='accumulation_periods', postgresql_where=sa.text('period_end IS NULL'))
