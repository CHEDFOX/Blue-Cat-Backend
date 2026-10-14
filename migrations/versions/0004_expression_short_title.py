"""expression short title

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14 08:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The short title in the expression's own language (e.g. the French title of a bilingual Act).
    op.add_column("instrument_versions", sa.Column("short_title", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("instrument_versions", "short_title")
