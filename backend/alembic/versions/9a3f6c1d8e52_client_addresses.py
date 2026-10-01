"""client IP (and MAC): session_state.address, pppoe_clients.last_address/last_mac

All nullable; filled from /ppp/active by the next poll.

Revision ID: 9a3f6c1d8e52
Revises: 7c1e4a9b2d30
Create Date: 2026-09-25 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "9a3f6c1d8e52"
down_revision: Union[str, None] = "7c1e4a9b2d30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("session_state", sa.Column("address", sa.String(45), nullable=True))
    op.add_column("pppoe_clients", sa.Column("last_address", sa.String(45), nullable=True))
    op.add_column("pppoe_clients", sa.Column("last_mac", sa.String(17), nullable=True))


def downgrade() -> None:
    op.drop_column("pppoe_clients", "last_mac")
    op.drop_column("pppoe_clients", "last_address")
    op.drop_column("session_state", "address")
