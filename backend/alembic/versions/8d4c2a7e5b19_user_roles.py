"""user roles: "full" or "readonly"

Existing users (the admin created at install time) get "full".

Revision ID: 8d4c2a7e5b19
Revises: 6b2d9e4f1a83
Create Date: 2026-09-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d4c2a7e5b19"
down_revision: Union[str, None] = "6b2d9e4f1a83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("role", sa.String(16), nullable=False, server_default="full"))
    op.create_check_constraint("ck_users_role", "users", "role IN ('full', 'readonly')")


def downgrade() -> None:
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role")
