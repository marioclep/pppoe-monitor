"""alert directions: thresholds and events per direction (download/upload)

- alert_thresholds.direction: at most one global threshold per direction and
  one per client and direction. Existing thresholds compared download+upload
  added together, which no longer exists: they become download thresholds
  (at the time of this change no installation had any).
- alert_events.direction and .threshold_bytes (the threshold it crossed,
  copied in), and threshold_id becomes nullable with ON DELETE SET NULL, so
  deleting a threshold keeps the alerts it produced.

Revision ID: 3f6a9c2e8d41
Revises: 8d4c2a7e5b19
Create Date: 2026-09-30 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3f6a9c2e8d41"
down_revision: Union[str, None] = "8d4c2a7e5b19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DIRECTION_CHECK = "direction IN ('download', 'upload')"


def upgrade() -> None:
    op.add_column("alert_thresholds", sa.Column("direction", sa.String(16), nullable=False, server_default="download"))
    op.alter_column("alert_thresholds", "direction", server_default=None)
    op.create_check_constraint("ck_alert_thresholds_direction", "alert_thresholds", DIRECTION_CHECK)
    op.drop_index("uq_alert_thresholds_single_global", table_name="alert_thresholds")
    op.drop_index("uq_alert_thresholds_client_id", table_name="alert_thresholds")
    op.create_index(
        "uq_alert_thresholds_client_direction", "alert_thresholds", ["client_id", "direction"],
        unique=True, postgresql_where=sa.text("client_id IS NOT NULL"),
    )
    op.create_index(
        "uq_alert_thresholds_global_direction", "alert_thresholds", ["direction"],
        unique=True, postgresql_where=sa.text("client_id IS NULL"),
    )

    op.add_column("alert_events", sa.Column("direction", sa.String(16), nullable=False, server_default="download"))
    op.alter_column("alert_events", "direction", server_default=None)
    op.create_check_constraint("ck_alert_events_direction", "alert_events", DIRECTION_CHECK)
    op.add_column("alert_events", sa.Column("threshold_bytes", sa.BigInteger(), nullable=True))
    op.execute(
        "UPDATE alert_events e SET threshold_bytes = t.bytes_threshold "
        "FROM alert_thresholds t WHERE t.id = e.threshold_id"
    )
    op.execute("UPDATE alert_events SET threshold_bytes = accumulated_bytes_at_trigger WHERE threshold_bytes IS NULL")
    op.alter_column("alert_events", "threshold_bytes", nullable=False)
    op.alter_column("alert_events", "threshold_id", nullable=True)
    op.drop_constraint("alert_events_threshold_id_fkey", "alert_events", type_="foreignkey")
    op.create_foreign_key(
        "alert_events_threshold_id_fkey", "alert_events", "alert_thresholds",
        ["threshold_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    # Back to one threshold per client (and one global): keep download.
    op.execute("DELETE FROM alert_thresholds WHERE direction = 'upload'")
    op.execute("DELETE FROM alert_events WHERE threshold_id IS NULL")
    op.drop_constraint("alert_events_threshold_id_fkey", "alert_events", type_="foreignkey")
    op.create_foreign_key(
        "alert_events_threshold_id_fkey", "alert_events", "alert_thresholds",
        ["threshold_id"], ["id"], ondelete="CASCADE",
    )
    op.alter_column("alert_events", "threshold_id", nullable=False)
    op.drop_column("alert_events", "threshold_bytes")
    op.drop_constraint("ck_alert_events_direction", "alert_events", type_="check")
    op.drop_column("alert_events", "direction")

    op.drop_index("uq_alert_thresholds_global_direction", table_name="alert_thresholds")
    op.drop_index("uq_alert_thresholds_client_direction", table_name="alert_thresholds")
    op.create_index(
        "uq_alert_thresholds_client_id", "alert_thresholds", ["client_id"],
        unique=True, postgresql_where=sa.text("client_id IS NOT NULL"),
    )
    op.create_index(
        "uq_alert_thresholds_single_global", "alert_thresholds", [sa.text("(client_id IS NULL)")],
        unique=True, postgresql_where=sa.text("client_id IS NULL"),
    )
    op.drop_constraint("ck_alert_thresholds_direction", "alert_thresholds", type_="check")
    op.drop_column("alert_thresholds", "direction")
