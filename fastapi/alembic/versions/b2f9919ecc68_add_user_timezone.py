"""add user timezone

Revision ID: b2f9919ecc68
Revises: ca392df3bef1
Create Date: 2026-09-28 18:56:12.170677
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2f9919ecc68'
down_revision: Union[str, None] = 'ca392df3bef1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "timezone",
            sa.String(length=100),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "timezone")