"""valid_from basis

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-14 07:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "provision_versions",
        sa.Column("valid_from_basis", sa.String(length=16), server_default="source", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_provision_versions_valid_from_basis"),
        "provision_versions",
        "valid_from_basis IN ('source', 'observed')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_provision_versions_valid_from_basis"), "provision_versions", type_="check")
    op.drop_column("provision_versions", "valid_from_basis")
